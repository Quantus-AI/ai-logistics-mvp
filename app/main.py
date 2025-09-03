from fastapi import FastAPI, UploadFile, File, Form, Request, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional
import pandas as pd
from fastapi.templating import Jinja2Templates
from .optimizer import optimize_routes, build_map_html

app = FastAPI(title="AI Logistics MVP – v3", version="0.3.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

API_KEY = "demo0123"

def require_api_key(x_api_key: str = Header(...)):
    if x_api_key!= API_KEY:
        raise HTTPException(status_code = 403, details = "Invalid API Key")

class Location(BaseModel):
    lat: float
    lng: float

class Stop(BaseModel):
    id: str
    lat: float
    lng: float
    name: Optional[str] = None
    demand: int = 0
    tw_start: Optional[str] = None  # HH:MM
    tw_end: Optional[str] = None

class OptimizeRequest(BaseModel):
    depot: Location
    stops: List[Stop]
    vehicle_count: int = 1
    vehicle_capacity: int = 10
    depot_tw_start: Optional[str] = None
    depot_tw_end: Optional[str] = None

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def upload(request: Request,
                 file: UploadFile = File(...),
                 depot_lat: float = Form(51.5072),
                 depot_lng: float = Form(-0.1276),
                 vehicle_count: int = Form(2),
                 vehicle_capacity: int = Form(5),
                 depot_tw_start: str = Form("08:00"),
                 depot_tw_end: str = Form("18:00")):
    df = pd.read_csv(file.file)
    requied_cols = {"id", "lat", "lng"}
    if not requied_cols.issubset(df.columns):
        return JSONResponse(
            {"error": "CSV must include id, lat, lng columns"},
            status_code = 400
        )
    stops = []
    for _, row in df.iterrows():
        stops.append((
            str(row.get("id")),
            float(row["lat"]),
            float(row["lng"]),
            row.get("name"),
            int(row.get("demand", 0)) if not pd.isna(row.get("demand", 0)) else 0,
            str(row.get("tw_start")) if not pd.isna(row.get("tw_start")) else None,
            str(row.get("tw_end")) if not pd.isna(row.get("tw_end")) else None
        ))
    result = optimize_routes(
        depot=(float(depot_lat), float(depot_lng)),
        stops=stops,
        vehicle_count=int(vehicle_count),
        vehicle_capacity=int(vehicle_capacity),
        depot_tw_start=depot_tw_start,
        depot_tw_end=depot_tw_end
    )
    build_map_html(result, out_path="templates/route_map.html")
    request.app.state.last_result = result
    return RedirectResponse(url="/result", status_code=303)

@app.get("/result")
def result_page(request: Request):
    result = getattr(request.app.state, "last_result", None)
    return templates.TemplateResponse("result.html", {"request": request, "result": result})

@app.post("/optimize")
def optimize(req: OptimizeRequest, auth = Depends(require_api_key)):
    stops = [(s.id, s.lat, s.lng, s.name, s.demand, s.tw_start, s.tw_end) for s in req.stops]
    result = optimize_routes(
        depot=(req.depot.lat, req.depot.lng),
        stops=stops,
        vehicle_count=req.vehicle_count,
        vehicle_capacity=req.vehicle_capacity,
        depot_tw_start=req.depot_tw_start,
        depot_tw_end=req.depot_tw_end
    )
    build_map_html(result, out_path="templates/route_map.html")
    return JSONResponse(result)

@app.post("/optimize_csv")
async def optimize_csv(file: UploadFile = File(...),
                       depot_lat: float = 51.5072,
                       depot_lng: float = -0.1276,
                       vehicle_count: int = 2,
                       vehicle_capacity: int = 5,
                       depot_tw_start: str = "08:00",
                       depot_tw_end: str = "18:00",
                       auth = Depends(require_api_key)):
    df = pd.read_csv(file.file)
    if len(df) > 500:
        return JSONResponse(
            {"error": "Too many stops (max 500 allowed)"},
            status_code = 400
        )
    stops = []
    for _, row in df.iterrows():
        stops.append((
            str(row.get("id")),
            float(row["lat"]),
            float(row["lng"]),
            row.get("name"),
            int(row.get("demand", 0)) if not pd.isna(row.get("demand", 0)) else 0,
            str(row.get("tw_start")) if not pd.isna(row.get("tw_start")) else None,
            str(row.get("tw_end")) if not pd.isna(row.get("tw_end")) else None
        ))
    result = optimize_routes(
        depot=(float(depot_lat), float(depot_lng)),
        stops=stops,
        vehicle_count=int(vehicle_count),
        vehicle_capacity=int(vehicle_capacity),
        depot_tw_start=depot_tw_start,
        depot_tw_end=depot_tw_end
    )
    build_map_html(result, out_path="templates/route_map.html")
    return JSONResponse(result)

@app.get("/map")
def get_map():
    try:
        with open("templates/route_map.html", "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(content=html, status_code=200)
    except FileNotFoundError:
        return HTMLResponse("<h3>No map generated yet. Upload/optimize first.</h3>")

@app.get("/download")
def download_results():
    result = getattr(app,state, "last_result", None)
    if not result:
        return JSONResponse(
            {"error": "No results yet"},
            status_code = 404
        )
    
    rows = []
    for v_idx, route in enumerate(result["routes"], start = 1):
        for stop in route:
            rows.append({"vehicle": v_idx, **stop})
    df = pd.DataFrame(rows)

    file_path = "data/result.xlsx"
    df.to_excel(file_path, index = False)
    return FileResponse(file_path, fileName = "optimized_routes.xlsx")