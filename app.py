# app.py
import os
import logging
import secrets
import re
import csv
import io
import requests

from flask import Flask, request, abort, jsonify, Response
from flask_cors import CORS
from supabase import create_client
from datetime import datetime, timezone

# ==================================================
# Auth
# ==================================================

def require_m():
    if request.headers.get("X-M-Key") != os.environ.get("M_API_KEY"):
        abort(403)

def require_c():
    if request.headers.get("X-C-Key") != os.environ.get("C_API_KEY"):
        abort(403)

def require_viewer():
    if (
        request.headers.get("X-M-Key") != os.environ.get("M_API_KEY")
        and request.headers.get("X-C-Key") != os.environ.get("C_API_KEY")
    ):
        abort(403)

# ==================================================
# App + DB
# ==================================================

app = Flask(__name__)
CORS(app, allow_headers=["Content-Type", "X-M-Key", "X-C-Key"])

logging.basicConfig(level=logging.INFO)

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

MAILGUN_DOMAIN = os.environ["MAILGUN_DOMAIN"]
MAILGUN_API_KEY = os.environ["MAILGUN_API_KEY"]
FROM_EMAIL = os.environ.get(
    "FROM_EMAIL", "Campaign <campaign@mg.renewableenergyx.com>"
)
REPLY_TO = os.environ.get(
    "REPLY_TO", "reply@mg.renewableenergyx.com"
)

EMAIL_RE = re.compile(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", re.I)

# ==================================================
# Helpers
# ==================================================

def extract_email(s: str) -> str:
    if not s:
        return ""
    m = EMAIL_RE.search(s)
    return m.group(1).lower() if m else ""

def clean_body(text: str) -> str:
    for marker in ("\nOn ", "\nFrom:", "\n>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

def gen_token() -> str:
    return secrets.token_hex(8)

def send_email(to_email: str, subject: str, body: str):
    resp = requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "text": body,
            "h:Reply-To": REPLY_TO,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()

def csv_response(filename: str, header: list, rows: list):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )

# ==================================================
# Routes
# ==================================================

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# ------------------ Mailgun webhook ------------------

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    sender = extract_email(
        request.form.get("sender") or request.form.get("from") or ""
    )
    subject = request.form.get("subject")
    body = request.form.get("body-plain")
    message_id = request.form.get("Message-Id")

    if not sender or not body or not message_id:
        return "OK", 200

    # dedupe
    if (
        supabase.table("replies")
        .select("id")
        .eq("message_id", message_id)
        .execute()
        .data
    ):
        return "OK", 200

    rec = (
        supabase.table("campaign_recipients")
        .select("campaign_id, token")
        .eq("email", sender)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )

    if not rec:
        return "OK", 200

    supabase.table("replies").insert({
        "campaign_id": rec[0]["campaign_id"],
        "recipient_email": sender,
        "token": rec[0]["token"],
        "subject": subject,
        "body": clean_body(body),
        "message_id": message_id,
    }).execute()

    supabase.table("campaign_recipients").update({
        "replied_at": "now()"
    }).eq("email", sender).execute()

    return "OK", 200

# ------------------ Replies (JSON) ------------------

@app.route("/replies", methods=["GET"])
def list_replies():
    require_viewer()

    campaign_id = request.args.get("campaign_id")

    q = (
        supabase.table("replies")
        .select("*")
        .order("received_at", desc=True)
        .limit(1000)
    )

    if campaign_id:
        q = q.eq("campaign_id", campaign_id)

    res = q.execute()
    return jsonify(res.data or [])




# ------------------ Replies CSV ------------------

@app.route("/campaigns/<cid>/replies.csv", methods=["GET"])
def replies_csv(cid):
    # viewer = M or C
    is_m = request.headers.get("X-M-Key") == os.environ.get("M_API_KEY")
    is_c = request.headers.get("X-C-Key") == os.environ.get("C_API_KEY")

    if not is_m and not is_c:
        abort(403)

    rows = (
        supabase.table("replies")
        .select("received_at,recipient_email,token,subject,body")
        .eq("campaign_id", cid)
        .order("received_at", desc=True)
        .execute()
        .data
        or []
    )

    # ---------------------------
    # M-UI: include emails
    # ---------------------------
    if is_m:
        return csv_response(
            f"campaign_{cid}_replies.csv",
            ["received_at", "recipient_email", "token", "subject", "body"],
            [
                [
                    r["received_at"],
                    r["recipient_email"],
                    r["token"],
                    r.get("subject", ""),
                    r.get("body", ""),
                ]
                for r in rows
            ],
        )

    # ---------------------------
    # C-UI: NO EMAILS
    # ---------------------------
    return csv_response(
        f"campaign_{cid}_replies.csv",
        ["received_at", "token", "subject", "body"],
        [
            [
                r["received_at"],
                r["token"],
                r.get("subject", ""),
                r.get("body", ""),
            ]
            for r in rows
        ],
    )


# ------------------ Campaigns ------------------

@app.route("/campaigns", methods=["GET"])
def list_campaigns():
    require_viewer()
    res = (
        supabase.table("campaigns")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify(res.data or [])

@app.route("/campaigns", methods=["POST"])
def create_campaign():
    require_viewer()
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    res = supabase.table("campaigns").insert({
        "name": name,
    }).execute()

    return jsonify(res.data[0]), 200

@app.route("/campaigns/<cid>/content", methods=["POST"])
def set_content(cid):
    require_c()
    subject = (request.json or {}).get("subject", "").strip()
    body = (request.json or {}).get("body", "").strip()

    if not subject or not body:
        return jsonify({"error": "subject and body required"}), 400

    supabase.table("campaigns").update({
        "subject": subject,
        "body": body,
        "status": "ready",
    }).eq("id", cid).execute()

    return jsonify({"status": "ready"}), 200

# ------------------ Upload emails ------------------

@app.route("/campaigns/<cid>/upload-emails", methods=["POST"])
def upload_emails(cid):
    require_m()

    data = request.get_json(force=True)
    emails = data.get("emails", [])

    if not emails:
        return jsonify({"error": "no emails provided"}), 400

    campaign = (
        supabase.table("campaigns")
        .select("*")
        .eq("id", cid)
        .single()
        .execute()
        .data
    )

    if not campaign:
        return jsonify({"error": "campaign not found"}), 404

    rows = []
    for email in emails:
        rows.append({
            "campaign_id": cid,
            "email": email,
            "token": secrets.token_hex(8),
        })

    supabase.table("campaign_recipients") \
        .upsert(
            rows,
            on_conflict="campaign_id,email",
            ignore_duplicates=True
        ) \
        .execute()

    return jsonify({
        "uploaded": len(rows)
    }), 200


    return jsonify({
        "uploaded": inserted
    }), 200

@app.route("/campaigns/<cid>/send", methods=["POST"])
def send_campaign(cid):
    require_m()

    campaign = (
        supabase.table("campaigns")
        .select("*")
        .eq("id", cid)
        .single()
        .execute()
        .data
    )

    if not campaign:
        return jsonify({"error": "campaign not found"}), 404

    if campaign["status"] != "ready":
        return jsonify({"error": "campaign not ready"}), 400

    recipients = (
        supabase.table("campaign_recipients")
        .select("*")
        .eq("campaign_id", cid)
        .execute()
        .data
    )

    # ✅ Filter unsent recipients in Python
    recipients = [r for r in recipients if r["sent_at"] is None]


    if not recipients:
        return jsonify({"error": "no recipients uploaded"}), 400

    sent = 0
    failed = 0

    now = datetime.now(timezone.utc).isoformat()
    for r in recipients:
        try:
            send_email(
                r["email"],
                campaign["subject"],
                campaign["body"],
            )


            supabase.table("campaign_recipients") \
                .update({"sent_at": now}) \
                .eq("id", r["id"]) \
                .execute()


            sent += 1
        except Exception as e:
            app.logger.exception(f"Failed to send to {r['email']}")
            failed += 1

    new_status = "sent" if failed == 0 else "partial"

    supabase.table("campaigns") \
        .update({
            "status": new_status,
            "sent_at": now,
        }) \
        .eq("id", cid) \
        .execute()


    return jsonify({
        "sent": sent,
        "failed": failed,
    }), 200


# ------------------ Recipients CSV ------------------

@app.route("/campaigns/<cid>/recipients.csv", methods=["GET"])
def recipients_csv(cid):
    require_m()

    rows = (
        supabase.table("campaign_recipients")
        .select("email,token,created_at,sent_at,replied_at")
        .eq("campaign_id", cid)
        .order("created_at")
        .execute()
        .data
        or []
    )

    return csv_response(
        f"campaign_{cid}_email_token_map.csv",
        ["email", "token", "created_at", "sent_at", "replied_at"],
        [
            [
                r["email"],
                r["token"],
                r["created_at"],
                r.get("sent_at"),
                r.get("replied_at"),
            ]
            for r in rows
        ],
    )

# ------------------ Send campaign ------------------

# @app.route("/campaigns/<cid>/send", methods=["POST"])
# def send_campaign(cid):
#     require_m()

#     camp = (
#         supabase.table("campaigns")
#         .select("*")
#         .eq("id", cid)
#         .single()
#         .execute()
#         .data
#     )

#     if camp["status"] != "ready":
#         return jsonify({"error": "campaign not ready"}), 400

#     recs = (
#         supabase.table("campaign_recipients")
#         .select("*")
#         .eq("campaign_id", cid)
#         .execute()
#         .data
#     )

#     sent = failed = 0

#     for r in recs:
#         try:
#             send_email(r["email"], camp["subject"], camp["body"])
#             sent += 1
#             supabase.table("campaign_recipients").update({
#                 "sent_at": "now()"
#             }).eq("id", r["id"]).execute()
#         except Exception:
#             failed += 1

#     supabase.table("campaigns").update({
#         "status": "sent",
#         "sent_at": "now()"
#     }).eq("id", cid).execute()

#     return jsonify({"sent": sent, "failed": failed}), 200

# ------------------ ADMIN: Clear all data ------------------
@app.route("/admin/clear-all", methods=["POST"])
def clear_all_data():
    require_m()

    data = request.get_json(force=True) or {}
    if data.get("confirm") != "DELETE_ALL_DATA":
        return jsonify({"error": "confirmation required"}), 400

    supabase.rpc("truncate_all_campaign_data").execute()
    return jsonify({"status": "all data cleared"}), 200



# ==================================================
# Main
# ==================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
