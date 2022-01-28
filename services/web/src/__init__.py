import os
import csv
import requests
from datetime import datetime
from flask import Flask, render_template, request as flask_request
from flask.wrappers import Response
import openai
from slack_sdk.signature import SignatureVerifier
from slack_sdk.errors import SlackApiError
from slack_sdk import WebClient
import json
from multiprocessing import Process

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
openai.api_key = os.environ["OPENAI_API_KEY"]


app = Flask(__name__)


class Slack:
    client = WebClient(token=SLACK_BOT_TOKEN)
    signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)


slack = Slack()


def genie_respond(prompt, original_message, response_url, thread_ts=None):
    try:
        rules_as_bullet_points = "\n".join(
            [f"* {rule}" for rule in prompt.splitlines()])
        openai_prompt = f"""follow rules to reformat msg\nrules: ###{rules_as_bullet_points}###\nmsg: ###{original_message}###"""
        print("Sending prompt to OpenAI:", openai_prompt)
        completion = get_completion(openai_prompt, max_tokens=3000)
    except Exception as e:
        print('Error getting completion from OpenAI', e)
        return

    try:
        print('Sending response to Slack:', completion.choices[0].text)
        requests.post(
            response_url,
            json={
                "text": completion.choices[0].text,
                "response_type": "in_channel",
                "replace_original": False,
                **({"thread_ts": thread_ts} if thread_ts else {}),
            },
            headers={
                'Content-Type': 'application/json',
                'Authentication': 'Bearer ' + SLACK_BOT_TOKEN
            }
        )
    except SlackApiError as e:
        print(f"Error posting message: {e}")

    except Exception as e:
        print(f"Some other error: {e}")


def get_completion(prompt, max_tokens=300):
    return openai.Completion.create(
        model='text-davinci-003',
        prompt=prompt,
        temperature=0.7,
        max_tokens=max_tokens,
    )


@ app.route("/", methods=("GET", "POST"))
def index():
    if flask_request.method == "POST":
        prompt = flask_request.form["prompt"]
        selected_model = 'text-davinci-003'

        response = get_completion(
            make_new_tweet_prompt(prompt), max_tokens=3000)

        print('prompts', make_new_tweet_prompt(prompt))
        print('response', response)

        write_history(
            response.created,
            prompt.strip(),
            response.choices[0].text.strip(),
            selected_model
        )

        return render_template(
            "index.html",
            history=read_history(),
            prompt=prompt,
        )

    return render_template(
        "index.html",
        history=read_history()
    )


# endpoint called by slack
@ app.route("/genie", methods=["POST"])
def genie():
    if not slack.signature_verifier.is_valid_request(flask_request.get_data(), flask_request.headers):
        return Response(status=403)

    if 'payload' in flask_request.form:
        payload = json.loads(flask_request.form['payload'])

        if payload["type"] == "message_action":
            print(payload['message'])
            thread_ts = payload["message"]["thread_ts"] if "thread_ts" in payload["message"] else None

            try:
                slack.client.views_open(
                    trigger_id=payload["trigger_id"],
                    view={
                        "type": "modal",
                        "callback_id": "modal-id",
                        "title": {
                            "type": "plain_text",
                            "text": "Genie",
                        },
                        "submit": {
                            "type": "plain_text",
                            "text": "Submit",
                        },
                        "close": {
                            "type": "plain_text",
                            "text": "Cancel",
                        },
                        "blocks": [
                            {
                                "block_id": "block-prompt",
                                "type": "input",
                                "label": {
                                    "type": "plain_text",
                                    "text": "How to convert it?",
                                },
                                "hint": {
                                    "type": "plain_text",
                                    "text": "Each line is treated as a rule."
                                },
                                "element": {
                                    "type": "plain_text_input",
                                    "action_id": "prompt",
                                    "multiline": True,
                                    "focus_on_load": True,
                                    "placeholder": {
                                        "type": "plain_text",
                                        "text": "format as bullet points\nsummarize the message\nremove unnecessary details",
                                    }
                                },
                                "optional": False
                            },
                            {
                                "block_id": "block-original-message",
                                "type": "input",
                                "element": {
                                    "type": "plain_text_input",
                                    "action_id": "original_message",
                                    "initial_value": payload['message']['text'].strip(),
                                    "multiline": True
                                },
                                "label": {
                                    "type": "plain_text",
                                    "text": "Original",
                                    "emoji": True
                                },
                                "hint": {
                                    "type": "plain_text",
                                    "text": "You can edit or keep as is."
                                },
                                "optional": True
                            },
                            *([
                                {
                                    "block_id": "block-post-to-thread",
                                    "type": "input",
                                    "optional": True,
                                    "element": {
                                        "type": "checkboxes",
                                        "action_id": "post_to_thread",
                                        "options": [
                                            {
                                                "text": {
                                                    "type": "plain_text",
                                                    "text": "When checked will post to this thread"
                                                },
                                                "value": thread_ts
                                            }
                                        ],
                                        "initial_options": [
                                            {
                                                "text": {
                                                    "type": "plain_text",
                                                    "text": "When checked will post to this thread"
                                                },
                                                "value": thread_ts
                                            }
                                        ],
                                    },
                                    "label": {
                                        "type": "plain_text",
                                        "text": "Post result to this thread?"
                                    }
                                }
                            ] if thread_ts else []),
                            {
                                "block_id": "block-respond-to",
                                "type": "input",
                                "optional": True,
                                "label": {
                                    "type": "plain_text",
                                    "text": "Where to show result?"
                                },
                                **({
                                    "hint": {
                                        "type": "plain_text",
                                        "text": "Only works if above checkbox is unchecked."
                                    }
                                } if thread_ts else {}),
                                "element": {
                                    "type": "conversations_select",
                                    "action_id": "respond_to_conversation",
                                    "response_url_enabled": True,
                                    "default_to_current_conversation": True,
                                }
                            }
                        ]
                    }
                )
                return Response(status=200)
            except SlackApiError as e:
                code = e.response["error"]
                return Response(f'Failed to open modal due to {code}', status=500)

        if payload['type'] == 'view_submission' and payload['view']['callback_id'] == "modal-id":
            print('view_submission payload', payload)
            response_url = payload['response_urls'][0]['response_url']
            values = payload['view']['state']['values']
            original_message = values['block-original-message']['original_message']['value']
            prompt = values['block-prompt']['prompt']['value']
            try:
                thread_ts = values['block-post-to-thread']['post_to_thread']['selected_options'][0]['value']
            except:
                thread_ts = None

            print('Preparing to send response to', response_url)
            task_cb = Process(target=genie_respond, args=(
                prompt, original_message, response_url, thread_ts))
            task_cb.start()

            return Response(status=200)

    return Response(status=404)


def make_new_tweet_prompt(tweet):
    return f"""Create 3 fun and engaging tweets for @QuestDB audience.

Topics: {tweet}
Tweet: """


HISTORY_CSV = "history.csv"


def write_history(timestamp, prompt, result, model):
    # if the file doesn't exist, create it
    write_mode = "a" if os.path.exists(HISTORY_CSV) else "w"
    with open(HISTORY_CSV, write_mode) as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow([timestamp, model, prompt, result])


def read_history():
    history = []
    if os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV) as f:
            reader = csv.reader(f, delimiter=",")
            for [timestamp, *rest] in reader:
                history.append((datetime.fromtimestamp(int(timestamp)), *rest))

        history.reverse()

    return history
