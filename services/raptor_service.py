from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
import json
import logging
import os
from datetime import datetime

# ─── File logger setup ────────────────────────────────────────────────────────
_raptor_log = logging.getLogger("raptor_debug")
if not _raptor_log.handlers:
    _fh = logging.FileHandler("raptor_debug.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    _raptor_log.addHandler(_fh)
    _raptor_log.setLevel(logging.DEBUG)
    _raptor_log.propagate = False

def _sec(s): 
    """Format seconds as HH:MM:SS for readability in logs"""
    if s == float("inf"): return "inf"
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"
# ──────────────────────────────────────────────────────────────────────────────

MAX_WALKING_DISTANCE_M = 500  # Max distance to walk between transfers
WALKING_SPEED_MS = 1.4
MAX_RESULTS = 5
MAX_TRANSFERS = 3
SEARCH_WINDOW_HOURS = 4
TRANSFER_TIME = 180  # 3 minutes buffer

class RaptorService:
    def __init__(self, timetable):
        self.timetable = timetable
        _raptor_log.info(
            f"[INIT] Timetable loaded: "
            f"{len(timetable.stops)} stops, "
            f"{len(timetable.trips)} trips, "
            f"{len(timetable.stop_times_by_trip)} trips with stop_times"
        )
        self.transfers = self._build_transfer_graph()

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371e3
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    def _build_transfer_graph(self):
        transfers = defaultdict(list)
        stops = list(self.timetable.stops.items())
        print(f"Building transfer graph for {len(stops)} stops...")
        count = 0
        for i in range(len(stops)):
            id1, s1 = stops[i]
            for j in range(i + 1, len(stops)):
                id2, s2 = stops[j]
                if abs(float(s1["lat"]) - float(s2["lat"])) > 0.01: continue
                if abs(float(s1["lon"]) - float(s2["lon"])) > 0.01: continue
                dist = self.haversine(
                    float(s1["lat"]), float(s1["lon"]),
                    float(s2["lat"]), float(s2["lon"])
                )
                if dist <= MAX_WALKING_DISTANCE_M:
                    walk_time = int(dist / WALKING_SPEED_MS)
                    transfers[id1].append((id2, walk_time))
                    transfers[id2].append((id1, walk_time))
                    count += 1
        _raptor_log.info(f"[INIT] Transfer graph: {count} connections across {len(transfers)} stops")
        print(f"Transfer graph built: {count} connections found.")
        return transfers

    def find_nearby_stops(self, lat, lon, max_distance=MAX_WALKING_DISTANCE_M):
        nearby = []
        for stop_id, stop in self.timetable.stops.items():
            stop_lat = float(stop["lat"])
            stop_lon = float(stop["lon"])
            distance = self.haversine(lat, lon, stop_lat, stop_lon)
            if distance <= max_distance:
                walking_time = int(distance / WALKING_SPEED_MS)
                nearby.append({
                    "stop_id": stop_id,
                    "stop": stop,
                    "distance": distance,
                    "walking_time": walking_time
                })
        nearby.sort(key=lambda x: x["distance"])
        return nearby[:15]

    @staticmethod
    def time_to_seconds(time_str):
        h, m, s = map(int, time_str.split(":"))
        return h*3600 + m*60 + s

    @staticmethod
    def has_duplicate_route_transfer(legs):
        transit_legs = [leg for leg in legs if leg["type"] == "transit"]
        for i in range(len(transit_legs) - 1):
            if transit_legs[i]["route_id"] == transit_legs[i+1]["route_id"]:
                return True
        return False

    @staticmethod
    def _get_transit_signature(legs):
        transit_legs = [leg for leg in legs if leg["type"] == "transit"]
        return tuple((leg["route_id"], leg["from_stop_id"], leg["to_stop_id"]) for leg in transit_legs)

    @staticmethod
    def _filter_duplicate_routes_different_walk(results):
        transit_groups = defaultdict(list)
        for result in results:
            signature = RaptorService._get_transit_signature(result["legs"])
            transit_groups[signature].append(result)

        filtered = []
        for group in transit_groups.values():
            if len(group) == 1:
                filtered.append(group[0])
            else:
                min_walk_result = min(
                    group,
                    key=lambda r: next((leg["duration_seconds"] for leg in r["legs"] if leg["type"] == "walk"), 0)
                )
                filtered.append(min_walk_result)
        return filtered

    @staticmethod
    def _merge_consecutive_walk_legs(legs):
        if not legs:
            return legs
        merged = []
        i = 0
        while i < len(legs):
            leg = legs[i]
            if leg["type"] == "walk":
                consecutive_walks = [leg]
                j = i + 1
                while j < len(legs) and legs[j]["type"] == "walk":
                    consecutive_walks.append(legs[j])
                    j += 1
                if len(consecutive_walks) >= 2:
                    first_walk = consecutive_walks[0]
                    last_walk = consecutive_walks[-1]
                    total_distance = sum(w["distance_m"] for w in consecutive_walks)
                    total_duration = sum(w["duration_seconds"] for w in consecutive_walks)
                    merged_leg = {
                        "type": "walk",
                        "from": first_walk["from"],
                        "to": last_walk["to"],
                        "distance_m": total_distance,
                        "duration_seconds": total_duration
                    }
                    merged.append(merged_leg)
                    i = j
                else:
                    merged.append(leg)
                    i += 1
            else:
                merged.append(leg)
                i += 1
        return merged

    def run(self, origin_lat, origin_lon, dest_lat, dest_lon, departure_time_seconds, debug=False):
        debug_logs = []
        req_id = datetime.now().strftime("%H%M%S")

        _raptor_log.info(
            f"[{req_id}] ═══ NEW REQUEST ═══ "
            f"origin=({origin_lat:.4f},{origin_lon:.4f}) "
            f"dest=({dest_lat:.4f},{dest_lon:.4f}) "
            f"depart={_sec(departure_time_seconds)}"
        )

        origin_stops = self.find_nearby_stops(origin_lat, origin_lon)
        dest_stops = self.find_nearby_stops(dest_lat, dest_lon)
        dest_stop_ids = {s["stop_id"] for s in dest_stops}

        if not origin_stops:
            _raptor_log.warning(f"[{req_id}] DEAD END: No origin stops found within {MAX_WALKING_DISTANCE_M}m")
            return {"routes": [], "debug_logs": debug_logs} if debug else []
        if not dest_stops:
            _raptor_log.warning(f"[{req_id}] DEAD END: No destination stops found within {MAX_WALKING_DISTANCE_M}m")
            return {"routes": [], "debug_logs": debug_logs} if debug else []

        tau = defaultdict(lambda: [float("inf")] * (MAX_TRANSFERS + 2))
        parent = defaultdict(lambda: [{} for _ in range(MAX_TRANSFERS + 2)])

        # Initialize walking from Origin
        for o in origin_stops:
            stop_id = o["stop_id"]
            arrival = departure_time_seconds + o["walking_time"]
            tau[stop_id][0] = arrival
            parent[stop_id][0] = {
                "type": "walk",
                "from_lat": origin_lat,
                "from_lon": origin_lon,
                "to_stop": o,
                "arrival": arrival
            }

        trips_by_route = defaultdict(list)
        for trip_id, stop_times in self.timetable.stop_times_by_trip.items():
            route_id = self.timetable.trips[trip_id].route_id
            sorted_stops = sorted(stop_times, key=lambda st: self.time_to_seconds(st.departure_time))
            stop_pattern = tuple(st.stop_id for st in sorted_stops)
            virtual_route_id = f"{route_id}_{hash(stop_pattern)}"
            trips_by_route[virtual_route_id].append((trip_id, sorted_stops))

        _raptor_log.info(f"[{req_id}] Virtual routes to scan: {len(trips_by_route)}")

        # --- RAPTOR Main Loop ---
        for k in range(1, MAX_TRANSFERS + 2):
            
            # Carry over best times from previous round
            for s in list(tau.keys()):
                tau[s][k] = tau[s][k-1]
                parent[s][k] = parent[s][k-1]

            marked_stops = {stop_id for stop_id in tau if tau[stop_id][k-1] < float("inf")}
            _raptor_log.info(f"[{req_id}] Round k={k}: {len(marked_stops)} marked stops")

            if not marked_stops:
                break

            stops_updated_by_transit = set()
            routes_scanned = 0
            routes_skipped_no_marked = 0
            trips_boarded = 0
            skip_counts = {"too_early": 0, "transfer_buffer": 0, "not_best": 0}

            # --- PHASE 1: TRANSIT ---
            for route_id, trips in trips_by_route.items():
                sample_stops = trips[0][1]
                if not any(st.stop_id in marked_stops for st in sample_stops):
                    routes_skipped_no_marked += 1
                    continue

                routes_scanned += 1
                best_trip = None
                best_boarding_stop = None
                best_boarding_time = float("inf")
                best_boarding_idx = -1

                for trip_id, stop_times in trips:
                    first_stop_time = self.time_to_seconds(stop_times[0].departure_time)

                    for idx, st in enumerate(stop_times):
                        stop_id = st.stop_id
                        if stop_id not in marked_stops: continue

                        dep_time_raw = self.time_to_seconds(st.departure_time)
                        # Shift trips that numerically appear in the past to tomorrow
                        dep_time = dep_time_raw
                        if dep_time < departure_time_seconds - 3600: dep_time += 86400

                        earliest_arrival = tau[stop_id][k-1]

                        if dep_time < earliest_arrival:
                            skip_counts["too_early"] += 1
                            continue
                        if earliest_arrival + TRANSFER_TIME > dep_time:
                            skip_counts["transfer_buffer"] += 1
                            continue
                        if dep_time >= best_boarding_time:
                            skip_counts["not_best"] += 1
                            continue

                        best_trip = (trip_id, stop_times, first_stop_time)
                        best_boarding_stop = stop_id
                        best_boarding_time = dep_time
                        best_boarding_idx = idx
                        break

                if best_trip:
                    trips_boarded += 1
                    trip_id, stop_times, first_stop_time = best_trip
                    for idx in range(best_boarding_idx + 1, len(stop_times)):
                        st = stop_times[idx]
                        stop_id = st.stop_id

                        arr_time = self.time_to_seconds(st.arrival_time)
                        if arr_time < departure_time_seconds - 3600: arr_time += 86400

                        if arr_time > departure_time_seconds + (SEARCH_WINDOW_HOURS * 3600): continue

                        if arr_time < tau[stop_id][k]:
                            tau[stop_id][k] = arr_time
                            stops_updated_by_transit.add(stop_id)
                            parent[stop_id][k] = {
                                "type": "transit",
                                "trip_id": trip_id,
                                "boarding_stop": best_boarding_stop,
                                "boarding_time": best_boarding_time,
                                "boarding_st": stop_times[best_boarding_idx],
                                "arrival_stop": stop_id,
                                "arrival_time": arr_time,
                                "arrival_st": st
                            }

            _raptor_log.info(
                f"[{req_id}]   Transit phase: scanned={routes_scanned} boarded={trips_boarded} stops_updated={len(stops_updated_by_transit)}"
            )

            # --- PHASE 2: TRANSFERS ---
            transfers_made = 0
            for stop_id in stops_updated_by_transit:
                arrival_time = tau[stop_id][k]
                if stop_id in self.transfers:
                    for neighbor_id, walk_seconds in self.transfers[stop_id]:
                        walk_arrival = arrival_time + walk_seconds
                        if walk_arrival < tau[neighbor_id][k]:
                            tau[neighbor_id][k] = walk_arrival
                            transfers_made += 1
                            parent[neighbor_id][k] = {
                                "type": "transfer",
                                "from_stop_id": stop_id,
                                "to_stop_id": neighbor_id,
                                "arrival": walk_arrival,
                                "walk_time": walk_seconds,
                                "previous_leg": parent[stop_id][k]
                            }

        # --- Reconstruct Routes ---
        search_window_end = departure_time_seconds + (SEARCH_WINDOW_HOURS * 3600)
        candidate_routes = []

        for dest_id in dest_stop_ids:
            for k in range(MAX_TRANSFERS + 2):
                arrival_time = tau[dest_id][k]
                if departure_time_seconds < arrival_time <= search_window_end:
                    candidate_routes.append((arrival_time, dest_id, k))

        candidate_routes.sort(key=lambda x: x[0])
        temp_results = []

        for best_time, best_dest, best_round in candidate_routes:
            legs = []
            current_stop = best_dest
            current_round = best_round

            while current_round >= 0:
                if current_stop not in parent or not parent[current_stop][current_round]:
                    break

                leg_info = parent[current_stop][current_round]

                if leg_info["type"] == "walk":
                    legs.insert(0, {
                        "type": "walk",
                        "from": {"lat": leg_info["from_lat"], "lon": leg_info["from_lon"]},
                        "to": {
                            "lat": leg_info["to_stop"]["stop"]["lat"],
                            "lon": leg_info["to_stop"]["stop"]["lon"],
                            "stop_id": leg_info["to_stop"]["stop_id"],
                            "stop_name": leg_info["to_stop"]["stop"]["stop_name"]
                        },
                        "distance_m": leg_info["to_stop"]["distance"],
                        "duration_seconds": leg_info["to_stop"]["walking_time"]
                    })
                    break

                elif leg_info["type"] == "transfer":
                    from_stop = self.timetable.stops[leg_info["from_stop_id"]]
                    to_stop = self.timetable.stops[leg_info["to_stop_id"]]
                    legs.insert(0, {
                        "type": "walk",
                        "from": {"lat": float(from_stop["lat"]), "lon": float(from_stop["lon"]), "stop_id": leg_info["from_stop_id"], "stop_name": from_stop["stop_name"]},
                        "to": {"lat": float(to_stop["lat"]), "lon": float(to_stop["lon"]), "stop_id": leg_info["to_stop_id"], "stop_name": to_stop["stop_name"]},
                        "distance_m": leg_info["walk_time"] * WALKING_SPEED_MS,
                        "duration_seconds": leg_info["walk_time"]
                    })
                    current_stop = leg_info["from_stop_id"]

                elif leg_info["type"] == "transit":
                    boarding_st = leg_info["boarding_st"]
                    arrival_st = leg_info["arrival_st"]
                    route_id = self.timetable.trips[leg_info["trip_id"]].route_id
                    legs.insert(0, {
                        "type": "transit",
                        "route_id": route_id,
                        "trip_id": leg_info["trip_id"],
                        "from_stop_id": boarding_st.stop_id,
                        "to_stop_id": arrival_st.stop_id,
                        "from_stop_name": self.timetable.stops[boarding_st.stop_id]["stop_name"],
                        "to_stop_name": self.timetable.stops[arrival_st.stop_id]["stop_name"],
                        "departure_time": boarding_st.departure_time,
                        "arrival_time": arrival_st.arrival_time
                    })
                    current_stop = leg_info["boarding_stop"]
                    current_round -= 1

            # Final walk to destination
            dest_stop_info = next(d for d in dest_stops if d["stop_id"] == best_dest)
            legs.append({
                "type": "walk",
                "from": {"lat": dest_stop_info["stop"]["lat"], "lon": dest_stop_info["stop"]["lon"], "stop_id": best_dest, "stop_name": dest_stop_info["stop"]["stop_name"]},
                "to": {"lat": dest_lat, "lon": dest_lon},
                "distance_m": dest_stop_info["distance"],
                "duration_seconds": dest_stop_info["walking_time"]
            })

            legs = self._merge_consecutive_walk_legs(legs)
            total_time = best_time - departure_time_seconds + dest_stop_info["walking_time"]

            if total_time > 0 and not self.has_duplicate_route_transfer(legs):
                temp_results.append({
                    "dest_stop": best_dest,
                    "total_time": total_time,
                    "transfer_count": sum(1 for leg in legs if leg["type"] == "transit"),
                    "legs": legs
                })

        # Filter results for best options
        if temp_results:
            fastest_time = min(r["total_time"] for r in temp_results)
            min_transfers = min(r["transfer_count"] for r in temp_results)
            results = [r for r in temp_results if r["total_time"] <= fastest_time + 600 or r["transfer_count"] <= min_transfers]
            results = self._filter_duplicate_routes_different_walk(results)[:MAX_RESULTS]
            _raptor_log.info(f"[{req_id}] RETURNING {len(results)} routes")
            return results

        _raptor_log.warning(f"[{req_id}] RETURNING 0 ROUTES")
        return []