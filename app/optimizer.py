from typing import List, Tuple, Dict, Any, Optional
import math

try:
    from ortools.constraint_solver import routing_enums_pb2
    from ortools.constraint_solver import pywrapcp
    HAVE_OR_TOOLS = True
except Exception:
    HAVE_OR_TOOLS = False

import folium

LatLng = Tuple[float, float]
Stop = Tuple[str, float, float, Optional[str], int, Optional[str], Optional[str]]

MILES_PER_KM = 0.621371

def haversine_miles(a: LatLng, b: LatLng) -> float:
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    km = 2 * R * math.asin(math.sqrt(h))
    return km * MILES_PER_KM

def parse_hhmm(hhmm: Optional[str]) -> Optional[int]:
    if not hhmm or str(hhmm).strip() == "" or str(hhmm).lower() == "nan":
        return None
    hh, mm = str(hhmm).split(":")
    return int(hh) * 60 + int(mm)

def _greedy_order(depot: LatLng, stops: List[Stop]) -> List[int]:
    visited = set()
    order = []
    curr = depot
    while len(visited) < len(stops):
        best_idx = None
        best_dist = float("inf")
        for i, s in enumerate(stops):
            if i in visited:
                continue
            d = haversine_miles(curr, (s[1], s[2]))
            if d < best_dist:
                best_dist = d
                best_idx = i
        visited.add(best_idx)
        order.append(best_idx)
        curr = (stops[best_idx][1], stops[best_idx][2])
    return order

def optimize_routes(
    depot: LatLng,
    stops: List[Stop],
    vehicle_count: int = 1,
    vehicle_capacity: int = 10,
    depot_tw_start: Optional[str] = None,
    depot_tw_end: Optional[str] = None
) -> Dict[str, Any]:
    if len(stops) == 0:
        return {
            "engine": "none",
            "routes": [],
            "total_miles": 0.0,
            "baseline_miles": 0.0,
            "savings_pct": 0.0,
            "note": "no stops"
        }

    AVG_MPH = 20
    engine = "greedy-fallback"
    note = None
    routes: List[List[Dict[str, Any]]] = []
    total_miles: float = 0.0

    if HAVE_OR_TOOLS:
        points = [("depot", depot[0], depot[1], "Depot", 0, depot_tw_start, depot_tw_end)] + stops
        n = len(points)

        dist_mmile = [[0] * n for _ in range(n)]
        for i in range(n):
            ai = (points[i][1], points[i][2])
            for j in range(n):
                if i == j:
                    continue
                bj = (points[j][1], points[j][2])
                miles = haversine_miles(ai, bj)
                dist_mmile[i][j] = int(round(miles * 1000))

        manager = pywrapcp.RoutingIndexManager(n, vehicle_count, 0)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return dist_mmile[from_node][to_node]

        transit_cb_idx = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

        demands = [0] + [max(0, s[4]) for s in stops]

        def demand_callback(from_index):
            node = manager.IndexToNode(from_index)
            return demands[node]

        demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_cb_idx, 0, [max(1, vehicle_capacity)] * vehicle_count, True, "Capacity"
        )

        def travel_time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            mmiles = dist_mmile[from_node][to_node]
            miles = mmiles / 1000.0
            minutes = int(miles / (AVG_MPH / 60.0))
            return minutes

        time_cb_idx = routing.RegisterTransitCallback(travel_time_callback)
        horizon = 24 * 60
        routing.AddDimension(time_cb_idx, 30, horizon, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")

        depot_start = parse_hhmm(depot_tw_start) or 0
        depot_end = parse_hhmm(depot_tw_end) or horizon
        time_dim.CumulVar(manager.NodeToIndex(0)).SetRange(depot_start, depot_end)

        for i in range(1, n):
            start = parse_hhmm(points[i][5]) or 0
            end = parse_hhmm(points[i][6]) or horizon
            time_dim.CumulVar(manager.NodeToIndex(i)).SetRange(start, end)

        for v in range(vehicle_count):
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v)))
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v)))

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.FromSeconds(5)

        solution = routing.SolveWithParameters(search_params)
        if solution:
            engine = "ortools"
            total_mmiles = 0
            for v in range(vehicle_count):
                index = routing.Start(v)
                prev = 0
                vehicle_route: List[Dict[str, Any]] = []
                while not routing.IsEnd(index):
                    node = manager.IndexToNode(index)
                    if node != 0:
                        sid, lat, lng, name, demand, tws, twe = points[node]
                        vehicle_route.append({
                            "id": sid, "lat": lat, "lng": lng, "name": name, "demand": demand
                        })
                        total_mmiles += dist_mmile[prev][node]
                        prev = node
                    index = solution.Value(routing.NextVar(index))
                total_mmiles += dist_mmile[prev][0]
                if vehicle_route:
                    routes.append(vehicle_route)
            total_miles = round(total_mmiles / 1000.0, 2)
        else:
            note = "No feasible OR-Tools solution found; using greedy fallback."

    if engine == "greedy-fallback":
        order = _greedy_order(depot, stops)
        buckets = [[] for _ in range(max(1, vehicle_count))]
        for idx, s_idx in enumerate(order):
            buckets[idx % len(buckets)].append(s_idx)

        routes = []
        total = 0.0
        for b in buckets:
            curr = depot
            vehicle = []
            for idx in b:
                s = stops[idx]
                total += haversine_miles(curr, (s[1], s[2]))
                curr = (s[1], s[2])
                vehicle.append({"id": s[0], "lat": s[1], "lng": s[2], "name": s[3], "demand": s[4]})
            total += haversine_miles(curr, depot)
            if vehicle:
                routes.append(vehicle)
        total_miles = round(total, 2)
        if note is None:
            note = "Install OR-Tools to enforce capacity/time windows exactly."

    baseline = 0.0
    curr = depot
    for s in stops:
        baseline += haversine_miles(curr, (s[1], s[2]))
        curr = (s[1], s[2])
    baseline += haversine_miles(curr, depot)
    baseline_miles = round(baseline, 2)

    savings_pct = round(((baseline_miles - total_miles) / baseline_miles * 100), 2) if baseline_miles > 0 else 0.0

    result = {
        "engine": engine,
        "routes": routes,
        "total_miles": total_miles,
        "baseline_miles": baseline_miles,
        "savings_pct": savings_pct,
    }
    if note:
        result["note"] = note
    return result

def build_map_html(result: Dict[str, Any], out_path: str = "templates/route_map.html") -> str:
    all_points = []
    for route in result.get("routes", []):
        for s in route:
            all_points.append((s["lat"], s["lng"]))
    if not all_points:
        all_points = [(51.5072, -0.1276)]
    center = (sum(p[0] for p in all_points)/len(all_points), sum(p[1] for p in all_points)/len(all_points))
    m = folium.Map(location=center, zoom_start=12)
    for r_idx, route in enumerate(result.get("routes", []), start=1):
        coords = []
        for i, s in enumerate(route, start=1):
            folium.Marker(
                location=(s["lat"], s["lng"]),
                popup=f"Veh {r_idx} · Stop {i}: {s.get('name') or s['id']} (demand={s.get('demand',0)})",
                tooltip=f"{s.get('name') or s['id']}"
            ).add_to(m)
            coords.append((s["lat"], s["lng"]))
        if len(coords) >= 2:
            folium.PolyLine(locations=coords).add_to(m)
    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    m.save(out_path)
    return out_path
