import os
import logging
import requests
from flask import Flask, request, abort
from supabase import create_client
from flask_cors import CORS

# --------------------------------------------------
# Auth helpers
# --------------------------------------------------

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

# --------------------------------------------------
# App + DB
# --------------------------------------------------

app = Flask(__name__)


CORS(
    app,
    resources={r"/*": {"origins": [
        "http://localhost:5173",
        "http://localhost:5174",
        # later:
        # "https://m.yourdomain.com",
        # "https://c.yourdomain.com",
    ]}},
    allow_headers=[
        "Content-Type",
        "X-M-Key",
        "X-C-Key",
    ],
)

logging.basicConfig(level=logging.INFO)

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def create_token_for_campaign(campaign_id: str) -> str:
    token = os.urandom(8).hex()
    supabase.table("campaign_tokens").insert({
        "token": token,
        "campaign_id": campaign_id,
    }).execute()
    return token

def send_test_email(to_email: str, token: str):
    response = requests.post(
        f"https://api.mailgun.net/v3/{os.environ['MAILGUN_DOMAIN']}/messages",
        auth=("api", os.environ["MAILGUN_API_KEY"]),
        data={
            "from": "Campaign <campaign@mg.renewableenergyx.com>",
            "to": to_email,
            "subject": "Test campaign email",
            "text": (
                "Hello!\n\n"
                "This is a test campaign email.\n\n"
                "Reply to this message."
            ),
            "h:Reply-To": f"reply+{token}@mg.renewableenergyx.com",
        },
        timeout=10,
    )
    response.raise_for_status()

def clean_body(text: str) -> str:
    for marker in ("\nOn ", "\nFrom:", "\n>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# ------------------ Mailgun webhook ------------------

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    logging.info("Mailgun webhook received")

    recipient = request.form.get("recipient", "")
    token = None

    if recipient.startswith("reply+"):
        token = recipient.split("+", 1)[1].split("@", 1)[0]

    subject = request.form.get("subject")
    body = request.form.get("body-plain")
    message_id = request.form.get("Message-Id")

    if not token or not body or not message_id:
        return "OK", 200

    # Deduplicate by Message-Id
    existing = (
        supabase
        .table("replies")
        .select("id")
        .eq("message_id", message_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        logging.info("Duplicate reply ignored")
        return "OK", 200

    row = (
        supabase
        .table("campaign_tokens")
        .select("campaign_id")
        .eq("token", token)
        .limit(1)
        .execute()
    )

    campaign_id = row.data[0]["campaign_id"] if row.data else None

    supabase.table("replies").insert({
        "token": token,
        "campaign_id": campaign_id,
        "subject": subject,
        "body": clean_body(body),
        "message_id": message_id,
    }).execute()

    return "OK", 200

# ------------------ Replies ------------------

@app.route("/replies", methods=["GET"])
def list_replies():
    require_viewer()
    res = (
        supabase
        .table("replies")
        .select("token, body, subject, campaign_id, received_at")
        .order("received_at", desc=True)
        .limit(50)
        .execute()
    )
    return res.data

# ------------------ Campaigns ------------------

@app.route("/campaigns", methods=["GET"])
def list_campaigns():
    require_viewer()  # allows M OR C

    res = (
        supabase
        .table("campaigns")
        .select("id,name,created_at")
        .order("created_at", desc=True)
        .execute()
    )

    return res.data


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    require_m()
    data = request.get_json(force=True)
    name = data.get("name")

    if not name:
        return {"error": "name required"}, 400

    res = supabase.table("campaigns").insert({
        "name": name
    }).execute()

    return res.data[0]

# ------------------ Send tests ------------------

@app.route("/send-campaign-test", methods=["POST"])
def send_campaign_test():
    data = request.get_json(force=True)
    campaign_id = data.get("campaign_id")
    to_email = data.get("to_email")

    if not campaign_id or not to_email:
        return {"error": "campaign_id and to_email required"}, 400

    token = create_token_for_campaign(campaign_id)
    send_test_email(to_email, token)

    return {
        "status": "sent",
        "campaign_id": campaign_id,
        "token": token,
    }

@app.route("/campaigns/<campaign_id>/tokenize-and-send", methods=["POST"])
def tokenize_and_send(campaign_id):
    require_m()
    data = request.get_json(force=True)
    emails = data.get("emails")

    if not emails or not isinstance(emails, list):
        return {"error": "emails must be a list"}, 400

    if len(emails) > 100:
        return {"error": "max 100 emails per request"}, 400

    results = {
        "campaign_id": campaign_id,
        "sent": [],
        "failed": [],
    }

    for email in emails:
        try:
            token = create_token_for_campaign(campaign_id)
            send_test_email(email, token)
            results["sent"].append({
                "email": email,
                "token": token,
            })
        except Exception:
            results["failed"].append({
                "email": email,
                "error": "send_failed",
            })

    return results, 200


# --------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
