import celery
from celery import shared_task
from puzzles.models import Puzzle
from puzzles.spreadsheets import make_sheet
import slacker
import time
import sys
import logging

try:
    from herring.secrets import SECRETS
    # A token logged in as a legitimate user. Turns out that "bots" can't
    # do the things we want to automate!
    SLACK = slacker.Slacker(SECRETS['slack-user-token'])
except KeyError:
    print(
        "Couldn't find the SECRETS environment variable. This server won't be able "
        "to use Slack and Google Drive integrations."
    )
    SLACK = None


def post_local_and_global(local_channel, local_message, global_message):
    logging.warning("tasks: post_local_and_global(%s, %s, %s)", local_channel, local_message, global_message)

    try:
        response = SLACK.channels.join(local_channel)
        channel_id = response.body['channel']['id']
        SLACK.chat.post_message(channel_id, local_message, link_names=True, as_user=True)
    except Exception:
        # Probably the channel's already archived. Don't worry too much about it.
        logging.warning("tasks: failed to post to local channel (probably archived)", exc_info=True)

    response = SLACK.channels.join('puzzle-status')
    global_channel_id = response.body['channel']['id']
    SLACK.chat.post_message(global_channel_id, global_message, link_names=True, as_user=True)


@shared_task(rate_limit=0.5)
def post_answer(slug, answer):
    logging.warning("tasks: post_answer(%s, %s)", slug, answer)

    puzzle = Puzzle.objects.get(slug=slug)
    answer = answer.upper()
    local_message = ":tada: Confirmed answer: {}".format(answer)
    global_message = ':tada: Puzzle "{name}" (#{slug}) was solved! The answer is: {answer}'.format(
        answer=answer,
        slug=slug,
        name=puzzle.name
    )
    post_local_and_global(slug, local_message, global_message)


@shared_task(rate_limit=0.5)
def post_update(slug, updated_field, value):
    logging.warning("tasks: post_update(%s, %s, %s)", slug, updated_field, value)

    try:
        puzzle = Puzzle.objects.get(slug=slug)
    except Puzzle.DoesNotExist:
        return
    local_message = '{} set to: {}'.format(updated_field, value)
    global_message = '"{name}" (#{slug}) now has these {field}: {value}'.format(
        field=updated_field,
        value=value,
        slug=slug,
        name=puzzle.name
    )
    post_local_and_global(slug, local_message, global_message)


@shared_task(bind=True, max_retries=10, default_retry_delay=5, rate_limit=0.25)  # rate_limit is in tasks/sec
def create_puzzle_sheet_and_channel(self, slug):
    logging.warning("tasks: create_puzzle_sheet_and_channel(%s)", slug)

    try:
        puzzle = Puzzle.objects.get(slug=slug)
    except Exception as e:
        logging.error("tasks: Failed to retrieve puzzle when creating sheet and channel (may be retried) - %s", slug, exc_info=True)
        raise self.retry(exc=e)

    sheet_title = '{} {}'.format(puzzle.identifier(), puzzle.name)
    sheet_url = make_sheet(sheet_title).rsplit('?', 1)[0]

    puzzle.url = sheet_url
    puzzle.save()

    try:
        created = SLACK.channels.create(slug)
    except slacker.Error:
        created = SLACK.channels.join(slug)

    channel_id = created.body['channel']['id']
    puzzle_name = puzzle.name
    if len(puzzle_name) >= 30:
        puzzle_name = puzzle_name[:29] + '\N{HORIZONTAL ELLIPSIS}'

    topic = "{name} - Sheet: {sheet} - Puzzle: {url}".format(
        name=puzzle_name,
        url=puzzle.hunt_url,
        sheet=sheet_url
    )
    SLACK.channels.set_topic(channel_id, topic)
    
    response = SLACK.channels.join('puzzle-status')
    status_channel_id = response.body['channel']['id']

    new_channel_msg = 'New puzzle created: "{name}" (#{slug})'.format(
        slug=slug, name=puzzle.name
    )

    SLACK.chat.post_message(status_channel_id, new_channel_msg, link_names=True, as_user=True)
