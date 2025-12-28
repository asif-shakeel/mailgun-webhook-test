import logging
from flask import Flask, request
import os
from supabase import create_client
import requests


def send_test_email(to_email: str, token: str):
    response = requests.post(
        f"https://api.mailgun.net/v3/{os.environ['MAILGUN_DOMAIN']}/messages",
        auth=("api", os.environ["MAILGUN_API_KEY"]),
        data={
            "from": "Campaign <campaign@mg.renewableenergyx.com>",
            "to": to_email,
            "subject": "Test campaign email",
            "text": "Hello!\n\nThis is a test campaign email.\n\nReply to this message.",
            "h:Reply-To": f"reply+{token}@mg.renewableenergyx.com",
        },
        timeout=10,
    )

    response.raise_for_status()

app = Flask(__name__)
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

def clean_body(text: str) -> str:
    markers = [
        "\nOn ",
        "\nFrom:",
        "\n>",
    ]
    for m in markers:
        if m in text:
            text = text.split(m, 1)[0]
    return text.strip()

# Configure logging so Render shows it
logging.basicConfig(level=logging.INFO)

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    logging.info("=== Mailgun webhook received ===")

    # Log content type
    logging.info("Content-Type: %s", request.content_type)

    # Log form keys
    logging.info("Form keys: %s", list(request.form.keys()))

    # Log recipient
    # logging.info("Recipient: %s", request.form.get("recipient"))

    # Log body preview if present
    recipient = request.form.get("recipient", "")

    token = None
    if recipient.startswith("reply+") and "@" in recipient:
        token = recipient.split("reply+", 1)[1].split("@", 1)[0]

    logging.info("Reply token: %s", token)


    # Log raw payload size (this is the key diagnostic)
    raw = request.get_data()
    logging.info("Raw payload length: %d bytes", len(raw))

    logging.info("=== End webhook ===")


    subject = request.form.get("subject")
    body = request.form.get("body-plain")

    if token and body:
        row = (
            supabase
            .table("campaign_tokens")
            .select("campaign_id")
            .eq("token", token)
            .limit(1)
            .execute()
        )

        campaign_id = row.data[0]["campaign_id"] if row.data else None




        body = clean_body(body)
        message_id = request.form.get("Message-Id")

        supabase.table("replies").insert({
            "token": token,
            "body": body,
            "subject": subject,
            "campaign_id": campaign_id,
            "message_id": message_id,
        }).execute()





    return "OK", 200

@app.route("/replies", methods=["GET"])
def list_replies():
    res = (
        supabase
        .table("replies")
        .select("token, body, subject, received_at")
        .order("received_at", desc=True)
        .limit(50)
        .execute()
    )

    return res.data

@app.route("/campaigns", methods=["POST"])
def create_campaign():
    data = request.get_json(force=True)
    name = data.get("name")

    if not name:
        return {"error": "name required"}, 400

    res = supabase.table("campaigns").insert({
        "name": name
    }).execute()

    return res.data[0]

@app.route("/send-campaign-test", methods=["POST"])
def send_campaign_test():
    data = request.get_json(force=True)
    campaign_id = data.get("campaign_id")
    to_email = data.get("to_email")

    if not campaign_id or not to_email:
        return {"error": "campaign_id and to_email required"}, 400

    token = os.urandom(8).hex()
    supabase.table("campaign_tokens").insert({
        "token": token,
        "campaign_id": campaign_id,
    }).execute()


    send_test_email(
        to_email=to_email,
        token=token,
    )

    return {
        "status": "sent",
        "campaign_id": campaign_id,
        "token": token,
    }


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
