import logging
from flask import Flask, request
import os
from supabase import create_client

app = Flask(__name__)
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)


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
    logging.info("Recipient: %s", request.form.get("recipient"))

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
        supabase.table("replies").insert({
            "token": token,
            "body": body,
            "subject": subject,
        }).execute()



    return "OK", 200

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
