from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
import json
from datetime import datetime

MAX_WALKING_DISTANCE_M = 500  # Max distance to walk between transfers
WALKING_SPEED_MS = 1.4
MAX_RESULTS = 5
MAX_TRANSFERS = 3
SEARCH_WINDOW_HOURS = 4
TRANSFER_TIME = 180  # 3 minutes buffer

class RaptorService:
    def __init__(self, timetable):
        """
        timetable: instance of Timetables loaded in memory
        """
        self.timetable = timetable
        # Precompute transfers between stops once to save time per request
        self.transfers = self._build_transfer_graph()

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        """Distance in meters between two points"""
        R = 6371e3
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    def _build_transfer_graph(self):
        """
        Builds a graph of walking transfers between stops that are close to each other.
        Returns: {stop_id: [(neighbor_id, walk_seconds), ...]}
        """
        transfers = defaultdict(list)
        
        # Get list of (stop_id, stop_data) tuples
        stops = list(self.timetable.stops.items())
        
        print(f"Building transfer graph for {len(stops)} stops...")
        count = 0
        for i in range(len(stops)):
            id1, s1 = stops[i]
            for j in range(i + 1, len(stops)):
                id2, s2 = stops[j]
                
                # Fast box check to skip distant stops
                if abs(float(s1["lat"]) - float(s2["lat"])) > 0.01: continue
                if abs(float(s1["lon"]) - float(s2["lon"])) > 0.01: continue

                dist = self.haversine(
                    float(s1["lat"]), float(s1["lon"]),
                    float(s2["lat"]), float(s2["lon"])
                )
                
                if dist <= MAX_WALKING_DISTANCE_M:
                    walk_time = int(dist / WALKING_SPEED_MS)
                    # Add bidirectional transfer using the IDs we unpacked
                    transfers[id1].append((id2, walk_time))
                    transfers[id2].append((id1, walk_time))
                    count += 1
        
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
        """
        Merge consecutive walking legs into a single leg.
        Takes the 'from' of the first walk and 'to' of the last walk.
        """
        if not legs:
            return legs
        
        merged = []
        i = 0
        
        while i < len(legs):
            leg = legs[i]
            
            # If this is a walk leg, look ahead for consecutive walks
            if leg["type"] == "walk":
                consecutive_walks = [leg]
                j = i + 1
                
                # Collect all consecutive walk legs
                while j < len(legs) and legs[j]["type"] == "walk":
                    consecutive_walks.append(legs[j])
                    j += 1
                
                # If we have 2+ consecutive walks, merge them
                if len(consecutive_walks) >= 2:
                    first_walk = consecutive_walks[0]
                    last_walk = consecutive_walks[-1]
                    
                    # Calculate total distance and time
                    total_distance = sum(w["distance_m"] for w in consecutive_walks)
                    total_duration = sum(w["duration_seconds"] for w in consecutive_walks)
                    
                    # Create merged leg
                    merged_leg = {
                        "type": "walk",
                        "from": first_walk["from"],
                        "to": last_walk["to"],
                        "distance_m": total_distance,
                        "duration_seconds": total_duration
                    }
                    merged.append(merged_leg)
                    i = j  # Skip all the walks we just merged
                else:
                    # Only one walk, add it as-is
                    merged.append(leg)
                    i += 1
            else:
                # Not a walk leg, add it as-is
                merged.append(leg)
                i += 1
        
        return merged

    def run(self, origin_lat, origin_lon, dest_lat, dest_lon, departure_time_seconds, debug=False):
        debug_logs = []
        
        origin_stops = self.find_nearby_stops(origin_lat, origin_lon)
        dest_stops = self.find_nearby_stops(dest_lat, dest_lon)
        dest_stop_ids = {s["stop_id"] for s in dest_stops}

        if debug:
            debug_logs.append(f"\n=== DEBUG: Origin stops found: {len(origin_stops)} ===")

        if not origin_stops or not dest_stops:
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

        # Pre-group trips (Sort by TIME + Pattern)
        trips_by_route = defaultdict(list)
        for trip_id, stop_times in self.timetable.stop_times_by_trip.items():
            route_id = self.timetable.trips[trip_id].route_id
            
            # Sort by sequence/time
            sorted_stops = sorted(stop_times, key=lambda st: self.time_to_seconds(st.departure_time))
            
            # Pattern signature
            stop_pattern = tuple(st.stop_id for st in sorted_stops)
            virtual_route_id = f"{route_id}_{hash(stop_pattern)}"
            trips_by_route[virtual_route_id].append((trip_id, sorted_stops))

        # --- RAPTOR Main Loop ---
        for k in range(1, MAX_TRANSFERS + 2):
            
            # 1. Identify stops to process (Marked stops)
            marked_stops = {stop_id for stop_id in tau if tau[stop_id][k-1] < float("inf")}
            
            if not marked_stops:
                break
            
            stops_updated_by_transit = set()

            # --- PHASE 1: TRANSIT (Ride routes) ---
            for route_id, trips in trips_by_route.items():
                sample_stops = trips[0][1]
                if not any(st.stop_id in marked_stops for st in sample_stops):
                    continue
                
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
                        if dep_time_raw < first_stop_time - 43200: dep_time = dep_time_raw + 86400
                        else: dep_time = dep_time_raw
                        if dep_time < departure_time_seconds - 43200: dep_time += 86400
                        
                        earliest_arrival = tau[stop_id][k-1]
                        
                        if dep_time < earliest_arrival: continue
                        if earliest_arrival + TRANSFER_TIME > dep_time: continue
                        if dep_time >= best_boarding_time: continue

                        best_trip = (trip_id, stop_times, first_stop_time)
                        best_boarding_stop = stop_id
                        best_boarding_time = dep_time
                        best_boarding_idx = idx
                        break 

                if best_trip:
                    trip_id, stop_times, first_stop_time = best_trip
                    for idx in range(best_boarding_idx + 1, len(stop_times)):
                        st = stop_times[idx]
                        stop_id = st.stop_id
                        
                        arr_time_raw = self.time_to_seconds(st.arrival_time)
                        if arr_time_raw < first_stop_time - 43200: arr_time = arr_time_raw + 86400
                        else: arr_time = arr_time_raw
                        if arr_time < departure_time_seconds - 43200: arr_time += 86400
                        
                        if arr_time > departure_time_seconds + (SEARCH_WINDOW_HOURS * 3600): continue
                        
                        # Update arrival?
                        if arr_time < tau[stop_id][k]:
                            tau[stop_id][k] = arr_time
                            stops_updated_by_transit.add(stop_id) # Mark for walking phase
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

            # --- PHASE 2: TRANSFERS (Footpaths) ---
            # Walk from stops we just arrived at to nearby stops
            for stop_id in stops_updated_by_transit:
                arrival_time = tau[stop_id][k]
                
                if stop_id in self.transfers:
                    for neighbor_id, walk_seconds in self.transfers[stop_id]:
                        walk_arrival = arrival_time + walk_seconds
                        
                        if walk_arrival < tau[neighbor_id][k]:
                            tau[neighbor_id][k] = walk_arrival
                            parent[neighbor_id][k] = {
                                "type": "transfer",
                                "from_stop_id": stop_id,
                                "to_stop_id": neighbor_id,
                                "arrival": walk_arrival,
                                "walk_time": walk_seconds,
                                "previous_leg": parent[stop_id][k]
                            }
        
        # --- Reconstruct Routes ---
        candidate_routes = []
        search_window_end = departure_time_seconds + (SEARCH_WINDOW_HOURS * 3600)
        
        for dest_id in dest_stop_ids:
            for k in range(MAX_TRANSFERS + 2):
                arrival_time = tau[dest_id][k]
                if arrival_time < float("inf") and arrival_time <= search_window_end:
                    candidate_routes.append((arrival_time, dest_id, k))

        candidate_routes.sort(key=lambda x: x[0])

        results = []
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
                        "from": {
                            "lat": float(from_stop["lat"]), 
                            "lon": float(from_stop["lon"]),
                            "stop_id": leg_info["from_stop_id"],
                            "stop_name": from_stop["stop_name"]
                        },
                        "to": {
                            "lat": float(to_stop["lat"]), 
                            "lon": float(to_stop["lon"]),
                            "stop_id": leg_info["to_stop_id"],
                            "stop_name": to_stop["stop_name"]
                        },
                        "distance_m": leg_info["walk_time"] * WALKING_SPEED_MS,
                        "duration_seconds": leg_info["walk_time"]
                    })
                    
                    current_stop = leg_info["from_stop_id"]
                    # Do NOT decrement current_round

                elif leg_info["type"] == "transit":
                    boarding_st = leg_info["boarding_st"]
                    arrival_st = leg_info["arrival_st"]
                    route_id = self.timetable.trips[leg_info["trip_id"]].route_id

                    from_stop_name = self.timetable.stops[boarding_st.stop_id]["stop_name"]
                    to_stop_name = self.timetable.stops[arrival_st.stop_id]["stop_name"]
                    
                    legs.insert(0, {
                        "type": "transit",
                        "route_id": route_id,
                        "trip_id": leg_info["trip_id"],
                        "from_stop_id": boarding_st.stop_id,
                        "to_stop_id": arrival_st.stop_id,
                        "from_stop_name": from_stop_name,
                        "to_stop_name": to_stop_name,
                        "departure_time": boarding_st.departure_time,
                        "arrival_time": arrival_st.arrival_time
                    })

                    current_stop = leg_info["boarding_stop"]
                    current_round -= 1

            # Final walk
            dest_stop_info = next(d for d in dest_stops if d["stop_id"] == best_dest)
            final_walk_time = dest_stop_info["walking_time"]
            legs.append({
                "type": "walk",
                "from": {
                    "lat": dest_stop_info["stop"]["lat"],
                    "lon": dest_stop_info["stop"]["lon"],
                    "stop_id": best_dest,
                    "stop_name": dest_stop_info["stop"]["stop_name"]
                },
                "to": {"lat": dest_lat, "lon": dest_lon},
                "distance_m": dest_stop_info["distance"],
                "duration_seconds": final_walk_time
            })

            # MERGE CONSECUTIVE WALK LEGS HERE
            legs = self._merge_consecutive_walk_legs(legs)

            if self.has_duplicate_route_transfer(legs): continue

            total_time = best_time - departure_time_seconds + final_walk_time
            if total_time < 0 or total_time > SEARCH_WINDOW_HOURS * 3600: continue
            
            temp_results.append({
                "dest_stop": best_dest,
                "total_time": total_time,
                "transfer_count": sum(1 for leg in legs if leg["type"] == "transit"),
                "legs": legs
            })

        if temp_results:
            fastest_time = min(r["total_time"] for r in temp_results)
            threshold_time = fastest_time + 60
            min_transfers = min(r["transfer_count"] for r in temp_results)
            
            for result in temp_results:
                if result["total_time"] <= threshold_time or result["transfer_count"] <= min_transfers:
                    results.append(result)

            results = self._filter_duplicate_routes_different_walk(results)
            results = results[:MAX_RESULTS]

            clean_results = []
            for result in results:
                clean_results.append({
                    "dest_stop": result["dest_stop"],
                    "total_time": result["total_time"],
                    "legs": result["legs"]
                })
            
            if debug: return {"routes": clean_results, "debug_logs": debug_logs}
            return clean_results

        if debug: return {"routes": results, "debug_logs": debug_logs}
        return results