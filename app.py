#!/usr/bin/env python3
"""Hermes Tasks Dashboard - Flask app for viewing and managing Hermes cron jobs and todo tasks."""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CRON_JOBS_FILE = HERMES_HOME / "cron" / "jobs.json"
CRON_OUTPUT_DIR = HERMES_HOME / "cron" / "output"
TODOS_FILE = HERMES_HOME / "todos.json"
STATIC_DIR = Path(__file__).parent / "static"

TZ = timezone(timedelta(hours=8))  # UTC+8


def now_iso():
    return datetime.now(TZ).isoformat()


def ensure_todos():
    if not TODOS_FILE.exists():
        TODOS_FILE.write_text(json.dumps({"todos": [], "updated_at": now_iso()}, indent=2))


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


# ============== Cron Jobs API ==============


def _cron_validate(body):
    """Validate and return normalized cron job fields from request body."""
    name = (body.get("name") or "").strip() or "未命名任务"
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")

    # Schedule parsing
    schedule_raw = body.get("schedule", body.get("schedule_display", "60m"))
    minutes = 60
    if isinstance(schedule_raw, dict):
        minutes = schedule_raw.get("minutes", 60)
    elif isinstance(schedule_raw, str):
        # Parse "30m", "2h", "1d", "every 3h", etc.
        s = schedule_raw.lower().replace("every ", "").strip()
        if s.endswith("m"):
            minutes = int(s.rstrip("m"))
        elif s.endswith("h"):
            minutes = int(s.rstrip("h")) * 60
        elif s.endswith("d"):
            minutes = int(s.rstrip("d")) * 1440
        else:
            try:
                minutes = int(s)
            except ValueError:
                minutes = 60

    if minutes < 5:
        minutes = 5

    display = f"every {minutes}m"
    if minutes >= 1440:
        display = f"every {minutes // 1440}d"
    elif minutes >= 60:
        display = f"every {minutes // 60}h"

    deliver = body.get("deliver", "telegram")
    skills = body.get("skills", [])
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    enabled_toolsets = body.get("enabled_toolsets", None)
    if isinstance(enabled_toolsets, str):
        enabled_toolsets = [s.strip() for s in enabled_toolsets.split(",") if s.strip()]

    return {
        "name": name,
        "prompt": prompt,
        "schedule": {"kind": "interval", "minutes": minutes, "display": display},
        "schedule_display": display,
        "deliver": deliver,
        "skills": skills,
        "enabled_toolsets": enabled_toolsets,
        "enabled": True,
        "no_agent": False,
    }


@app.route("/api/cron", methods=["GET"])
def list_cron_jobs():
    data = load_json(CRON_JOBS_FILE)
    return jsonify(data)


@app.route("/api/cron", methods=["POST"])
def create_cron_job():
    try:
        body = request.get_json(force=True)
        fields = _cron_validate(body)
    except (ValueError, Exception) as e:
        return jsonify({"error": str(e)}), 400

    data = load_json(CRON_JOBS_FILE)
    jobs = data.get("jobs", [])

    now = now_iso()
    job = {
        "id": uuid.uuid4().hex[:12],
        "name": fields["name"],
        "prompt": fields["prompt"],
        "skills": fields["skills"],
        "skill": None,
        "model": body.get("model", None),
        "provider": None,
        "base_url": None,
        "script": None,
        "no_agent": fields["no_agent"],
        "context_from": None,
        "schedule": fields["schedule"],
        "schedule_display": fields["schedule_display"],
        "repeat": {"times": None, "completed": 0},
        "enabled": fields["enabled"],
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": now,
        "next_run_at": now,
        "last_run_at": None,
        "last_status": "pending",
        "last_error": None,
        "last_delivery_error": None,
        "deliver": fields["deliver"],
        "origin": None,
        "enabled_toolsets": fields["enabled_toolsets"],
        "workdir": None,
        "profile": None,
    }
    jobs.append(job)
    data["jobs"] = jobs
    data["updated_at"] = now
    write_json(CRON_JOBS_FILE, data)
    return jsonify(job), 201


@app.route("/api/cron/<job_id>", methods=["PUT"])
def update_cron_job(job_id):
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid json"}), 400

    data = load_json(CRON_JOBS_FILE)
    jobs = data.get("jobs", [])
    for job in jobs:
        if job["id"] == job_id:
            # Toggle enabled
            if "enabled" in body:
                job["enabled"] = bool(body["enabled"])
                if not job["enabled"]:
                    job["state"] = "paused"
                    job["paused_at"] = now_iso()
                else:
                    job["state"] = "scheduled"
                    job["paused_at"] = None
                    job["paused_reason"] = None
            if "name" in body:
                job["name"] = body["name"].strip() or job["name"]
            if "prompt" in body and body["prompt"].strip():
                job["prompt"] = body["prompt"].strip()
            if "deliver" in body:
                job["deliver"] = body["deliver"]
            if "schedule" in body or "schedule_display" in body:
                try:
                    fields = _cron_validate(body)
                    job["schedule"] = fields["schedule"]
                    job["schedule_display"] = fields["schedule_display"]
                except ValueError:
                    pass
            data["updated_at"] = now_iso()
            write_json(CRON_JOBS_FILE, data)
            return jsonify(job)
    return jsonify({"error": "not found"}), 404


@app.route("/api/cron/<job_id>", methods=["DELETE"])
def delete_cron_job(job_id):
    data = load_json(CRON_JOBS_FILE)
    jobs = data.get("jobs", [])
    new_jobs = [j for j in jobs if j["id"] != job_id]
    if len(new_jobs) == len(jobs):
        return jsonify({"error": "not found"}), 404
    data["jobs"] = new_jobs
    data["updated_at"] = now_iso()
    write_json(CRON_JOBS_FILE, data)
    return jsonify({"ok": True})


@app.route("/api/cron/<job_id>/output")
def get_cron_output(job_id):
    output_dir = CRON_OUTPUT_DIR / job_id
    if not output_dir.exists():
        return jsonify({"files": []})
    files = sorted(output_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:10]:
        content = ""
        try:
            content = f.read_text()[:10000] if f.stat().st_size < 50000 else "[file too large]"
        except Exception:
            content = "[read error]"
        result.append({
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "content": content,
        })
    return jsonify({"files": result})


# ============== Todo Tasks API ==============


@app.route("/api/todos", methods=["GET"])
def list_todos():
    ensure_todos()
    data = load_json(TODOS_FILE)
    return jsonify(data)


@app.route("/api/todos", methods=["POST"])
def create_todo():
    ensure_todos()
    body = request.get_json(force=True)
    data = load_json(TODOS_FILE)
    todos = data.get("todos", [])
    todo = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "content": (body.get("content") or "").strip(),
        "status": body.get("status", "pending"),
        "category": body.get("category", ""),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    todos.append(todo)
    data["todos"] = todos
    data["updated_at"] = now_iso()
    write_json(TODOS_FILE, data)
    return jsonify(todo), 201


@app.route("/api/todos/<todo_id>", methods=["PUT"])
def update_todo(todo_id):
    ensure_todos()
    body = request.get_json(force=True)
    data = load_json(TODOS_FILE)
    todos = data.get("todos", [])
    for todo in todos:
        if todo["id"] == todo_id:
            for key in ("content", "status", "category"):
                if key in body:
                    todo[key] = body[key]
            todo["updated_at"] = now_iso()
            data["updated_at"] = now_iso()
            write_json(TODOS_FILE, data)
            return jsonify(todo)
    return jsonify({"error": "not found"}), 404


@app.route("/api/todos/<todo_id>", methods=["DELETE"])
def delete_todo(todo_id):
    ensure_todos()
    data = load_json(TODOS_FILE)
    todos = data.get("todos", [])
    data["todos"] = [t for t in todos if t["id"] != todo_id]
    data["updated_at"] = now_iso()
    write_json(TODOS_FILE, data)
    return jsonify({"ok": True})


# ============== Static ==============


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    os.makedirs(STATIC_DIR, exist_ok=True)
    ensure_todos()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    print(f"Hermes Tasks Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
