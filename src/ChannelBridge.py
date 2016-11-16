import io
from threading import Thread

import time

import emoji as emoji
import yaml
from yaml.scanner import ScannerError

from yowsup.layers import YowLayerEvent
from yowsup.layers.auth import AuthError
from yowsup.layers.interface import YowInterfaceLayer, ProtocolEntityCallback
from yowsup.layers.network import YowNetworkLayer
from yowsup.layers.protocol_messages.protocolentities import TextMessageProtocolEntity
from slackclient import SlackClient
from yowsup.layers.protocol_profiles.protocolentities import SetStatusIqProtocolEntity
from yowsup.stacks import YowStackBuilder


def loadConfiguration():
    loadedConfiguration = False

    try:
        f = open('config.yaml')
        loadedConfiguration = yaml.safe_load(f)
        f.close()
    except ScannerError as e:
        print(e)

    return loadedConfiguration


# Load configuration
configuration = loadConfiguration()

if not configuration:
    print('Unable to load configuration')
    exit(-1)

# Initialize defaults
credentials = (str(configuration['config']['whatsapp']['number']), configuration['config']['whatsapp']['password'])
sc = SlackClient(configuration['config']['slack']['key'])
spamRateLimit = {}

# Channel bridge layer for Yowsup
class ChannelBridgeLayer(YowInterfaceLayer):

    @ProtocolEntityCallback("success")
    def onSuccess(self, successProtocolEntity):
        entity = SetStatusIqProtocolEntity('- Connects WhatsApp and Slack -')
        self._sendIq(entity)

    def sendMessage(self, to, content):
        outgoingMessage = TextMessageProtocolEntity(content, to=to)
        self.toLower(outgoingMessage)

    @ProtocolEntityCallback('message')
    def onMessage(self, messageProtocolEntity):
        postChannel = False

        print('Received WhatsApp message from ' + messageProtocolEntity.getAuthor(False)
              + ' in chat ' + messageProtocolEntity.getFrom())

        if messageProtocolEntity.getType() == 'text':
            print('Message was "' + emoji.demojize(messageProtocolEntity.getBody()) + '"')
        else:
            print('Message was of type ' + messageProtocolEntity.getType())

        for channel, config in configuration['channels'].items():
            if config['whatsapp'] == messageProtocolEntity.getFrom():
                postChannel = config['slack']
                break

        if postChannel:
            username = messageProtocolEntity.getAuthor(False)
            icon = None

            for contact, config in configuration['contacts'].items():
                if str(config['whatsapp']) == str(messageProtocolEntity.getAuthor(False)):
                    username = config['name']

                    if 'slack' in config:
                        profile = sc.api_call(
                            'users.info',
                            user=config['slack'],
                        )

                        icon = profile['user']['profile']['image_48']

                    break

            if messageProtocolEntity.getType() == 'text':
                sc.api_call(
                    'chat.postMessage',
                    channel=postChannel,
                    username=username,
                    icon_url=icon,
                    text=messageProtocolEntity.getBody()
                )
            elif messageProtocolEntity.getType() == 'media':
                if messageProtocolEntity.getMediaType() == "image":
                    sc.api_call(
                        'files.upload',
                        channels=postChannel,
                        file=io.BytesIO(messageProtocolEntity.getMediaContent())
                    )
                else:
                    print('Unsupported message media type passed through: ' + messageProtocolEntity.getType())
            else:
                print('Unsupported message type passed through: ' + messageProtocolEntity.getType())

        else:
            if messageProtocolEntity.getFrom() in spamRateLimit:
                spamRateLimit[messageProtocolEntity.getFrom()] -= 1

                if spamRateLimit[messageProtocolEntity.getFrom()] <= 0:
                    spamRateLimit[messageProtocolEntity.getFrom()] = 10
            else:
                spamRateLimit[messageProtocolEntity.getFrom()] = 10

            if spamRateLimit[messageProtocolEntity.getFrom()] == 10:
                self.sendMessage(messageProtocolEntity.getFrom(),
                                 'Are you tokking to me? Ik ken dit gesprek niet.. Bel Wouter even!')

        self.toLower(messageProtocolEntity.ack())
        self.toLower(messageProtocolEntity.ack(True))

    @ProtocolEntityCallback('receipt')
    def onReceipt(self, entity):
        self.toLower(entity.ack())


channelBridgeLayer = ChannelBridgeLayer()


def whatsapp():
    while True:
        try:

            stackBuilder = YowStackBuilder()

            stack = stackBuilder \
                .pushDefaultLayers(True) \
                .push(channelBridgeLayer) \
                .build()

            stack.setCredentials(credentials)
            stack.broadcastEvent(YowLayerEvent(YowNetworkLayer.EVENT_STATE_CONNECT))

            try:
                stack.loop()
            except AuthError as e:
                print('Authentication Error: %s' % e.message)

        except BaseException as e:
            print(e)


def slack():
    while True:
        try:
            if sc.rtm_connect():
                while True:
                    messages = sc.rtm_read()

                    for message in messages:

                        if message['type'] == 'message':
                            postChannel = False

                            if 'user' in message:
                                print('Received Slack message from ' + message['user']
                                      + ' in channel ' + message['channel'])
                            else:
                                print('Received Slack message from unknown in channel ' + message['channel'])

                            if 'subtype' in message:
                                print('Message was of subtype ' + message['subtype'])
                            else:
                                print('Message was "' + message['text'] + '"')

                            for channel, config in configuration['channels'].items():
                                if config['slack'] == message['channel']:
                                    postChannel = config['whatsapp']
                                    break

                            if postChannel:
                                prefix = None

                                if 'user' in message:
                                    # Try to find user in contacts
                                    for contact, config in configuration['contacts'].items():
                                        if 'slack' not in config:
                                            continue

                                        if str(config['slack']) == str(message['user']):
                                            prefix = config['name']

                                    # Get firstname and lastname from Slack
                                    if prefix is None:
                                        user = sc.api_call(
                                            'users.info',
                                            user=message['user'],
                                        )

                                        if 'user' in user:
                                            profile = user['user']['profile']
                                            if 'firstname' in profile and 'last_name' in profile:
                                                prefix = profile['real_name']

                                            if 'username' in user['user']:
                                                prefix = user['user']

                                if prefix is None:
                                    continue

                                if 'subtype' in message:
                                    if message['subtype'] == 'file_share':
                                        channelBridgeLayer.sendMessage(postChannel, prefix + ' shared a file on Slack.')
                                else:
                                    channelBridgeLayer.sendMessage(postChannel,
                                                                   prefix + ': ' + emoji.emojize(message['text'],
                                                                                                 use_aliases=True))
                            else:
                                if message['channel'] in spamRateLimit:
                                    spamRateLimit[message['channel']] -= 1

                                    if spamRateLimit[message['channel']] <= 0:
                                        spamRateLimit[message['channel']] = 10
                                else:
                                    spamRateLimit[message['channel']] = 10

                                if spamRateLimit[message['channel']] == 10:
                                    sc.api_call(
                                        'chat.postMessage',
                                        channel=message['channel'],
                                        username='whatsapp',
                                        text='Are you tokking to me? Ik ken dit gesprek niet.. Bel Wouter even!'
                                    )

                    time.sleep(1)
            else:
                print('Connection Failed, invalid token?')
        except BaseException as e:
            print(e)


if __name__ == '__main__':
    whatsappThread = Thread(target=whatsapp)
    slackThread = Thread(target=slack)

    whatsappThread.start()
    slackThread.start()

    while True:
        time.sleep(10)

        refreshConfiguration = loadConfiguration()

        if not refreshConfiguration:
            print('Unable to refresh configuration because of an error')
            continue

        configuration = refreshConfiguration
