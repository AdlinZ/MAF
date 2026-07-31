import json
import re
import uuid
from pathlib import Path


JM_TO_DISPLAY = {
    "minecraft:overworld": "主世界",
    "minecraft:the_nether": "下界",
    "minecraft:the_end": "末地",
}
DISPLAY_TO_JM = {value: key for key, value in JM_TO_DISPLAY.items()}


def discover_sources(root):
    root = Path(root)
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    sources = []
    for mode in ("mp", "sp"):
        mode_dir = data_dir / mode
        if not mode_dir.is_dir():
            continue
        for world_dir in mode_dir.iterdir():
            if world_dir.is_dir() and (world_dir / "waypoints").is_dir():
                sources.append(f"{mode}/{world_dir.name}")
    return sorted(sources, key=str.casefold)


def _number_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_waypoint(path, root):
    path = Path(path)
    root = Path(root)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dimensions = raw.get("dimensions") or ["minecraft:overworld"]
    dimension_id = str(dimensions[0])
    source = path.parent.parent.relative_to(root / "data").as_posix()
    mtime = path.stat().st_mtime
    return {
        "name": str(raw.get("name") or raw.get("id") or path.stem),
        "dimension": JM_TO_DISPLAY.get(dimension_id, dimension_id),
        "x": _number_text(raw.get("x", 0)),
        "y": _number_text(raw.get("y", 0)),
        "z": _number_text(raw.get("z", 0)),
        "note": "",
        "updated_at": mtime,
        "journeymap": {
            "id": str(raw.get("id") or path.stem),
            "source": source,
            "filename": path.name,
            "mtime": mtime,
            "raw": raw,
        },
    }


def scan_waypoints(root, source=None):
    root = Path(root)
    sources = [source] if source else discover_sources(root)
    results = []
    errors = []
    for item in sources:
        waypoint_dir = root / "data" / Path(item) / "waypoints"
        for path in sorted(waypoint_dir.glob("*.json"), key=lambda value: value.name.casefold()):
            try:
                results.append(read_waypoint(path, root))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append((str(path), str(exc)))
    return results, errors


def waypoint_key(coordinate):
    metadata = coordinate.get("journeymap") or {}
    waypoint_id = metadata.get("id")
    source = metadata.get("source")
    return f"{source}|{waypoint_id}" if source and waypoint_id else None


def _coordinate_number(coordinate, key):
    value = float(coordinate.get(key, 0))
    return int(value) if value.is_integer() else value


def build_payload(coordinate, waypoint_id=None):
    metadata = coordinate.get("journeymap") or {}
    payload = dict(metadata.get("raw") or {})
    waypoint_id = waypoint_id or metadata.get("id") or f"mcassistant_{uuid.uuid4().hex[:12]}"
    dimension = str(coordinate.get("dimension") or "主世界")
    dimension_id = DISPLAY_TO_JM.get(dimension, dimension)
    payload.update({
        "id": waypoint_id,
        "name": str(coordinate.get("name") or "未命名坐标"),
        "icon": payload.get("icon", "journeymap:ui/img/waypoint-icon.png"),
        "x": _coordinate_number(coordinate, "x"),
        "y": _coordinate_number(coordinate, "y"),
        "z": _coordinate_number(coordinate, "z"),
        "r": int(payload.get("r", 0)),
        "g": int(payload.get("g", 254)),
        "b": int(payload.get("b", 54)),
        "enable": bool(payload.get("enable", True)),
        "type": payload.get("type", "Normal"),
        "origin": payload.get("origin", "journeymap"),
        "dimensions": [dimension_id],
        "persistent": bool(payload.get("persistent", True)),
    })
    return payload


def _safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or f"waypoint_{uuid.uuid4().hex[:8]}"


def _atomic_json_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_waypoint(coordinate, root, source, output_dir=None):
    root = Path(root)
    metadata = coordinate.get("journeymap") or {}
    waypoint_id = str(metadata.get("id") or f"mcassistant_{uuid.uuid4().hex[:12]}")
    payload = build_payload(coordinate, waypoint_id)
    filename = metadata.get("filename") or f"{_safe_filename(waypoint_id)}.json"
    destination = Path(output_dir) if output_dir else root / "data" / Path(source) / "waypoints"
    path = destination / filename
    _atomic_json_write(path, payload)
    mtime = path.stat().st_mtime
    coordinate["journeymap"] = {
        "id": waypoint_id,
        "source": source,
        "filename": filename,
        "mtime": mtime,
        "raw": payload,
    }
    coordinate["updated_at"] = mtime
    return path


def sync_coordinates(coordinates, root, target_source):
    remote, errors = scan_waypoints(root, target_source) if target_source else scan_waypoints(root)
    local_by_key = {waypoint_key(item): item for item in coordinates if waypoint_key(item)}
    imported = 0
    updated = 0
    written = 0

    for remote_item in remote:
        key = waypoint_key(remote_item)
        local_item = local_by_key.get(key)
        if local_item is None:
            coordinates.append(remote_item)
            local_by_key[key] = remote_item
            imported += 1
            continue
        remote_mtime = remote_item["journeymap"]["mtime"]
        known_mtime = float((local_item.get("journeymap") or {}).get("mtime", 0))
        local_updated = float(local_item.get("updated_at", known_mtime))
        if remote_mtime > max(known_mtime, local_updated) + 0.001:
            note = local_item.get("note", "")
            local_item.update(remote_item)
            local_item["note"] = note
            updated += 1
        elif local_updated > remote_mtime + 0.001:
            write_waypoint(local_item, root, local_item["journeymap"]["source"])
            written += 1

    if target_source:
        for item in coordinates:
            if not waypoint_key(item):
                write_waypoint(item, root, target_source)
                written += 1

    return {"imported": imported, "updated": updated, "written": written, "errors": errors}


def export_waypoints(coordinates, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    exported = []
    used_names = set()
    for item in coordinates:
        metadata = item.get("journeymap") or {}
        waypoint_id = str(metadata.get("id") or f"mcassistant_{uuid.uuid4().hex[:12]}")
        base = _safe_filename(waypoint_id)
        filename = f"{base}.json"
        counter = 2
        while filename.casefold() in used_names:
            filename = f"{base}_{counter}.json"
            counter += 1
        used_names.add(filename.casefold())
        path = destination / filename
        _atomic_json_write(path, build_payload(item, waypoint_id))
        exported.append(path)
    return exported
