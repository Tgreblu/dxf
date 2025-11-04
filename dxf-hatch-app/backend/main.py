from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import io
import ezdxf

app = FastAPI(title="DXF Hatch Generator")

# CORS aperto per sviluppo (puoi restringere ai tuoi domini in produzione)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerateParams(BaseModel):
    center_x: float = Field(0.0, description="Centro X (mm)")
    center_y: float = Field(0.0, description="Centro Y (mm)")
    radius: float = Field(10.0, gt=0, description="Raggio (mm)")
    spacing: float = Field(0.2, gt=0, description="Passo linee (mm)")
    angle_deg: float = Field(45.0, description="Angolo pattern (gradi)")
    layer_circle: str = Field("CIRCLE", description="Layer cerchio")
    layer_hatch: str = Field("HATCH", description="Layer hatch")
    version: str = Field("R2018", description="Versione DXF (es. R2010, R2013, R2018)")

def _doc_with_units(version: str = "R2018") -> ezdxf.EzDxfDocument:
    doc = ezdxf.new(setup=True, version=version)
    # Imposta unità in millimetri (INSUNITS = 4)
    doc.header["$INSUNITS"] = 4
    return doc

def _make_layers(doc: ezdxf.EzDxfDocument, layer_names: list[str]) -> None:
    for name in layer_names:
        if name not in doc.layers:
            doc.layers.new(name=name)

def _add_circle_with_hatch(
    doc: ezdxf.EzDxfDocument,
    cx: float,
    cy: float,
    r: float,
    spacing: float,
    angle_deg: float,
    layer_circle: str,
    layer_hatch: str,
) -> None:
    msp = doc.modelspace()

    # 1) Crea il cerchio
    circle = msp.add_circle(center=(cx, cy), radius=r, dxfattribs={"layer": layer_circle})

    # 2) Crea l’HATCH associativo con pattern predefinito ANSI31
    #    Nota: con ANSI31 lo "scale" equivale alla spaziatura base del pattern in unità disegno.
    #    SICCOME LAVORIAMO IN mm, impostiamo scale = spacing (0,2 mm) => linee a 0,2 mm.
    hatch = msp.add_hatch(dxfattribs={"layer": layer_hatch})
    hatch.set_associative(True)

    # Boundary = il cerchio appena creato
    with hatch.edit_boundary() as e:
        e.add_circle((cx, cy), r)

    # Pattern: ANSI31 con ANGLE=45° e SCALE=spacing
    # (Questa è l'opzione più semplice e molto compatibile)
    hatch.set_pattern_fill("ANSI31", angle=angle_deg, scale=spacing)

    # --- ALTERNATIVA (commentata) — pattern "user-defined" esplicito con spacing esatto
    #    Se vuoi forzare un pattern personalizzato invece di ANSI31:
    #    L’offset (dx, dy) è il vettore perpendicolare alla linea; per 45°, usa (±s/√2, s/√2).
    #    NB: lasciare ANSI31 è spesso più portabile tra CAD e viewer.
    #
    # import math
    # s = spacing
    # theta = math.radians(angle_deg + 90.0)  # perpendicolare alle linee di 45°
    # dx = s * math.cos(theta)
    # dy = s * math.sin(theta)
    # hatch.set_pattern_definition([
    #     # (angle, x, y, dx, dy, dash_items) -> dash_items vuoto = linea continua
    #     (angle_deg, 0.0, 0.0, dx, dy, []),
    # ])

@app.post("/generate")
def generate(params: GenerateParams):
    doc = _doc_with_units(version=params.version)
    _make_layers(doc, [params.layer_circle, params.layer_hatch])

    _add_circle_with_hatch(
        doc,
        cx=params.center_x,
        cy=params.center_y,
        r=params.radius,
        spacing=params.spacing,
        angle_deg=params.angle_deg,
        layer_circle=params.layer_circle,
        layer_hatch=params.layer_hatch,
    )

    # Restituisci il DXF come file
    buf = io.BytesIO()
    doc.saveas(buf)
    buf.seek(0)
    filename = f"circle_hatch_{params.spacing}mm_{int(params.angle_deg)}deg.dxf"
    return StreamingResponse(
        buf,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.post("/hatch-on-upload")
def hatch_on_upload(
    file: UploadFile = File(...),
    spacing: float = 0.2,
    angle_deg: float = 45.0,
    layer_hatch: str = "HATCH",
):
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="Carica un file .dxf")
    content = file.file.read()
    try:
        doc = ezdxf.read(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"DXF non valido: {e}")

    # Crea layer se manca
    _make_layers(doc, [layer_hatch])

    # Trova il primo CIRCLE nel modelspace
    msp = doc.modelspace()
    first_circle = None
    for e in msp:
        if e.dxftype() == "CIRCLE":
            first_circle = e
            break
    if first_circle is None:
        raise HTTPException(status_code=400, detail="Nessun CIRCLE trovato nel DXF caricato.")

    # Aggiungi l'HATCH associativo sul cerchio trovato
    hatch = msp.add_hatch(dxfattribs={"layer": layer_hatch})
    hatch.set_associative(True)
    with hatch.edit_boundary() as ed:
        ed.add_circle(first_circle.dxf.center, first_circle.dxf.radius)
    hatch.set_pattern_fill("ANSI31", angle=angle_deg, scale=spacing)

    buf = io.BytesIO()
    doc.saveas(buf)
    buf.seek(0)
    filename = f"{file.filename.rsplit('.dxf',1)[0]}_HATCHED.dxf"
    return StreamingResponse(
        buf,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
