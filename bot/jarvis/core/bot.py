import logging
import sys
import time

import slackclient

import jarvis.core.helper as helper
import jarvis.core.messages as messages
import jarvis.db.channels as channels
import jarvis.db.schema as schema
import jarvis.db.users as users
from jarvis.plugins import get_plugins


# MonkeyPatch for slackclient not properly supporting Python 3
slackclient._channel.Channel.__hash__ = lambda self: hash(self.id)  # pylint: disable=W0212


logger = logging.getLogger(__name__)


__version__ = '2.0.0'


class Jarvis(object):
    def __init__(self, token):
        self.last_ping = 0
        self.uuid = None
        self.plugins = None

        try:
            self.slack = slackclient.SlackClient(token)
            self.slack.rtm_connect()

            self.uuid = self.slack.api_call('auth.test')['user_id']
        except Exception as e:
            logger.error('Could not start JARVIS.')
            logger.exception(e)
            sys.exit(1)

    def init(self):
        schema.initialize()

        user_list = self.slack.api_call('users.list')
        for user in user_list['members']:
            if user['deleted']:
                continue

            if user['is_bot'] or user['id'] == 'USLACKBOT':
                continue

            user = helper.get_user_fields(self.slack, user)
            users.UsersDal.create(*user)

    def handle_message(self, channel, text, user):
        ch = helper.get_channel_or_fail(logger, self.slack, channel)

        if 'help' in text:
            message = [messages.DESCRIBE(__version__)]
            for plugin in self.plugins:
                message.append('- {}'.format(plugin.name))
                if plugin.name in text:
                    plugin.help(ch=ch)
                    break
            else:
                ch.send_message('\n'.join(message))
            return

        for plugin in self.plugins:
            plugin.respond(ch=ch, user=user, msg=text)

    def input(self, data):
        try:
            user = data.get('user')
            if user == self.uuid:
                return

            kind = data.get('type')
            if kind == 'message':
                ch = data.get('channel')
                text = data.get('text', '').lower()


                direct = channels.ChannelsDal.is_direct(ch)
                mention = text.startswith('jarvis')
                if direct or mention:
                    self.handle_message(ch, text, user)

                return

            if kind in ('team_join', 'user_change'):
                user = helper.get_user_fields(self.slack, user)

                users.UsersDal.update(*user)
                return
        except Exception:
            logger.error('Error handling message %s', str(data))
            raise

    def keepalive(self):
        now = int(time.time())
        if now > self.last_ping + 3:
            self.slack.server.ping()
            self.last_ping = now

    def run(self):
        self.plugins = get_plugins(self.slack)

        while True:
            try:
                for message in self.slack.rtm_read():
                    self.input(message)

                self.keepalive()
                time.sleep(.1)
            except Exception as e:
                logger.error('Caught unhandled exception, exiting...')
                logger.exception(e)

                for channel in channels.ChannelsDal.read(admin_only=True):
                    c = helper.get_channel_or_fail(logger, self.slack, channel)
                    c.send_message(messages.DEATH(e).encode('ascii', 'ignore'))

                sys.exit(1)
