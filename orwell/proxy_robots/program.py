import argparse
import zmq
import orwell.messages.robot_pb2 as robot_messages
import orwell.messages.server_game_pb2 as server_game_messages
import orwell.messages.controller_pb2 as controller_messages
import collections
from enum import Enum


class Messages(Enum):
    Register = 'Register'
    Registered = 'Registered'
    Input = 'Input'


REGISTRY = {
    Messages.Register.name: lambda: robot_messages.Register(),
    Messages.Registered.name: lambda: server_game_messages.Registered(),
    Messages.Input.name: lambda: controller_messages.Input(),
}


class Subscriber(object):
    def __init__(self, address, port, context):
        self._socket = context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SUBSCRIBE, "")
        url = "tcp://%s:%i" % (address, port)
        self._socket.connect(url)

    def read(self):
        return self._socket.recv(flags=zmq.DONTWAIT)


class Pusher(object):
    def __init__(self, address, port, context):
        self._socket = context.socket(zmq.PUSH)
        self._socket.setsockopt(zmq.LINGER, 0)
        url = "tcp://%s:%i" % (address, port)
        self._socket.connect(url)

    def write(self, message):
        self._socket.send(message)


class MessageHub(object):
    """
    Class that is in charge of orchestrating reads and writes.
    Items that are to be written are provided with #post and
    objects that want to be notified of reads listen through #register.
    """
    def __init__(
            self,
            publisher_port,
            puller_port,
            address,
            subscriber_type=Subscriber,
            pusher_type=Pusher):
        """
        `publisher_port`: port to read from.
        `puller_port`: port to write to.
        `address`: address used for both reads and writes.
        `subscriber_type`: for testing purpose ; class to use as a subscriber
          which reads from the publisher port.
        `pusher_type`: for testing purpose ; class to use as pusher which
          writes to the puller port.
        """
        self._context = zmq.Context()
        self._pusher = pusher_type(
            address,
            puller_port,
            self._context)
        self._subscriber = subscriber_type(
            address,
            publisher_port,
            self._context)
        self._listeners = collections.defaultdict(list)
        self._outgoing = []

    def register(self, listener, message_type, routing_id):
        """
        `listener`: object which has a #notify method (which takes a message
            type, a routing id and a decoded protobuf message as arguments).
        `message_type`: the types of messages the listener is intersted in.
            If empty means all types are intersting.
        `routing_id`: the routing ids the listener is intersted in. If empty
            means all ids are intersting.
        Tell that #listener wants to be notified of messages read for type
        #message_type and routind id #routing_id.
        """
        #print 'MessageHub.register({0}, {1}, {2}'.format(
            #listener, message_type, routing_id)
        if ((listener, routing_id) not in self._listeners[message_type]):
            self._listeners[message_type].append((routing_id, listener))

    def unregister(self, listener, message_type, routing_id):
        """
        Reverts the effects of #register (the parameters must be the same).
        """
        if ((listener, routing_id) in self._listeners[message_type]):
            self._listeners[message_type].remove((listener, routing_id))

    def post(self, payload):
        """
        Put a message (type + routing id + encode protobuf message) in the list
        of messages to write to the pusher.
        """
        self._outgoing.append(payload)

    def step(self):
        """
        Process one incomming message (if any) and process all outgoing
        messages (if any).
        """
        debug = True
        if (debug):
            print 'MessageHub.step()'
            print '_listeners =', self._listeners
        string = self._subscriber.read()
        if (debug):
            print 'string =', repr(string)
        if (string is not None):
            message_type, routing_id, raw_message = string.split(' ', 2)
            if (debug):
                print 'message_type =', message_type
                print 'routing_id =', routing_id
            message = REGISTRY[message_type]()
            message.ParseFromString(raw_message)
            for expected_routing_id, listener in self._listeners[message_type]:
                if (debug):
                    print 'listener =', listener
                    print 'expected_routing_id =', expected_routing_id
                if (expected_routing_id):
                    is_expected = True
                else:
                    is_expected = (expected_routing_id == routing_id)
                if (is_expected):
                    listener.notify(message_type, routing_id, message)
        for payload in self._outgoing:
            self._pusher.write(payload)
        del self._outgoing[:]


class Proxy(object):
    """
    Helper class.
    """
    def __init__(
            self,
            message_hub,
            callback,
            message_type,
            routing_id):
        self.message_hub = message_hub
        self.callback = callback
        self.message_type = message_type
        self.routing_id = routing_id

    def register(self, action):
        self.message_hub.register(action, self.message_type, self.routing_id)

    def unregister(self, action):
        self.message_hub.unregister(action, self.message_type, self.routing_id)


class Status(Enum):
    # just created
    created = 0
    # action called, but no reply yet
    pending = 1
    # action called, reply received
    waiting = 2
    # action failed
    failed = 3
    # action successful
    successful = 4


class Action(object):
    """
    Object functor to wrap a function and possibly the notification associated
    to the function (the function sends the message and the notification is
    triggered when the reply is received).
    """
    def __init__(
            self,
            doer,
            success,
            proxy=None,
            repeat=False):
        """
        `doer`: the function that does something.
        `success`: the function to call to check if the action is successful
            or not.
        `proxy`: the object containing information about the notification to
            register to (if needed). If None, there is no registration.
        `repeat`: True if and only if the action is to be attempted again on
            failure. The function #doer is called again when this happends.
        """
        self._doer = doer
        self._success = success
        self._repeat = repeat
        self._proxy = proxy
        self._status = Status.created
        if (self._proxy):
            self._proxy.register(self)

    def call(self):
        """
        Call the wrapped function.
        """
        self._doer()
        self._update_status()

    def reset(self):
        """
        To be called on failure to make it possible to repeat the action.
        """
        self._update_status()

    @property
    def status(self):
        """
        Status tracking where the action stands.
        """
        return self._status

    def _update_status(self):
        """
        Update the status of the action.
        """
        updated = False
        if (Status.created == self._status):
            if (self._proxy):
                self._status = Status.pending
            else:
                self._status = Status.waiting
            updated = True
        if (not updated):
            if (Status.pending == self._status):
                self._status = Status.waiting
            elif (self._status in (Status.successful, Status.failed)):
                self._status = Status.created
        if (Status.waiting == self._status):
            if (not self._proxy):
                if (self._success()):
                    self._status = Status.successful
                else:
                    self._status = Status.failed

    def notify(
            self,
            message_type,
            routing_id,
            message):
        """
        May only called if a proxy was provided to the constructor. Called when
        the message registered to is read.
        """
        #print 'Action.notify({0}, {1}, {2})'.format(
            #message_type,
            #routing_id,
            #message)
        if (self._proxy.message_type):
            if (self._proxy.message_type != message_type):
                raise Exception("Expected message type {0} but got {1}".format(
                    self._proxy.message_type, message_type))
        if (self._proxy.routing_id):
            if (self._proxy.routing_id != routing_id):
                raise Exception("Expected routing id {0} but got {1}".format(
                    self._proxy.routing_id, routing_id))
        self._update_status()
        self._proxy.callback(message_type, routing_id, message)
        self._update_status()
        self._proxy.unregister(self)


class Actionner(object):
    """
    Engine that makes the actions run.
    """
    def __init__(self):
        self._created_actions = []
        self._pending_actions = []

    def add_action(self, action):
        """
        Simply add an action to be run in the next call to #step.
        """
        self._created_actions.append(action)

    def step(self):
        """
        Check all pending actions to see if a notification has been received.
        Run all the actions that are in the created state.
        """
        #print 'Actionner.step()'
        #print '_created_actions =', self._created_actions
        #print '_pending_actions =', self._pending_actions
        poper = []
        new_actions = []
        for action in self._pending_actions:
            if (Status.waiting == action.status):
                action._update_status()
                poper.append(action)
                if (Status.successful == action.status):
                    pass
                elif (Status.failed == action.status):
                    if (action.repeat):
                        action.reset()
                        new_actions.append(action)
        for action in poper:
            self._pending_actions.remove(action)
        for action in self._created_actions:
            action.call()
            if (Status.pending == action.status):
                self._pending_actions.append(action)
            elif (Status.successful == action.status):
                pass
            elif (Status.failed == action.status):
                if (action.repeat):
                    action.reset()
                    new_actions.append(action)
        self._created_actions = new_actions


class Robot(object):
    def __init__(
            self,
            robot_id,
            message_hub,
            actionner,
            device):
        """
        `robot_id`: identifies the robot somehow.
        `message_hub`: used to post message and get notifications.
        `actionner`: object that will run the actions for the robot.
        `device`: deviced used to communicate with the robot.
        """
        self._robot_id = robot_id
        self._name = ''
        self._message_hub = message_hub
        self._actionner = actionner
        self._device = device
        self._registered = False
        self._left = 0.0
        self._right = 0.0
        self._fire1 = False
        self._fire2 = False
        self._previous_left = 0.0
        self._previous_right = 0.0
        self._previous_fire1 = False
        self._previous_fire2 = False

    @property
    def robot_id(self):
        return self._robot_id

    @property
    def name(self):
        return self._name

    @property
    def left(self):
        return self._left

    @property
    def right(self):
        return self._right

    @property
    def fire1(self):
        return self._fire1

    @property
    def fire2(self):
        return self._fire2

    def step(self):
        if ((self._previous_left != self._left) or
                (self._previous_right != self._right)):
            if (self._device):
                self._device.move(self._left, self._right)
            self._previous_left = self._left
            self._previous_right = self._right

    @property
    def registered(self):
        """
        True if and only if the robot has been registered in the game server.
        """
        return self._registered

    def queue_register(self):
        """
        Create an action that will take care of registering the robot and
        dispatch the notification.
        """
        #print 'queue_register'
        proxy = Proxy(
            self._message_hub,
            self.notify,
            Messages.Registered.name,
            self._robot_id)
        action = Action(
            self.register,
            lambda: self.registered,
            proxy,
            repeat=True)
        self._actionner.add_action(action)

    def register(self):
        """
        Post a message to ask for the registration of the robot.
        """
        message = REGISTRY[Messages.Register.name]()
        message.robot_id = self._robot_id
        payload = '{0} {1} {2}'.format(
            Messages.Register.name,
            self._robot_id,
            message.SerializeToString())
        self._message_hub.post(payload)

    def notify(
            self,
            message_type,
            routing_id,
            message):
        """
        Notifications dispatcher.
        """
        assert(self._robot_id == routing_id)
        if (Messages.Registered.name == message_type):
            self._notify_registered(message)
        elif (Messages.Input.name == message_type):
            self._notify_input(message)
        else:
            raise Exception("Invalid message type: " + message_type)

    def _notify_registered(self, message):
        """
        Flag the robot as registered if the server replied with a name.
        """
        if (message.name):
            self._registered = True
            self._name = message.name
            #print 'Robot registered (robot_id = {0} ; name = {1})'.format(
                #self._robot_id,
                #self._name)
            # this is a hack as we should only register when the game starts
            self._message_hub.register(
                self, Messages.Input.name, self._robot_id)

    def _notify_input(self, message):
        """
        Make the robot move.
        """
        print '_notify_input({0})'.format(message)
        self._left = message.move.left
        self._right = message.move.right
        self._fire1 = message.fire.weapon1
        self._fire2 = message.fire.weapon2

    #def move(self, left, right):
        #"""
        #Nothing yet.
        #"""
        #pass


class SocketsLister(object):
    """
    Class that for now lists bluetooth device and open the matching sockets.
    """
    def __init__(self):
        self._sockets = []
        self._sockets += self._discover_bluetooth()
        self._busy_map = [False for _ in self._sockets]

    # maybe the socket object does it itself
    #def __del__(self):
        #"""
        #Make sure we close all the sockets.
        #"""
        #for socket in self._sockets:
            #socket.close()

    def pop_available_socket(self):
        """
        Return the first available socket (or None if none is found).
        #You will be responsible of closing it.
        """
        available_socket = None
        if (self._sockets):
            available_socket = self._sockets.pop(0)
        return available_socket

    def _discover_bluetooth(self):
        import bluetooth
        import pprint
        pp = pprint.PrettyPrinter(indent=4)
        usable_sockets = []
        devices = bluetooth.discover_devices()
        for device in devices:
            service = bluetooth.find_service(address=device)
            if (service):
                info_map = service[0]
                pp.pprint(info_map)
                protocol = info_map['protocol']
                if ('RFCOMM' == protocol):
                    socket = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
                    host = info_map['host']
                    port = info_map['port']
                    socket.connect((host, port))
                    usable_sockets.append(socket)
        return usable_sockets


class MoveOrder(Enum):
    POWER = 1
    SPEED = 2


class Motors(Enum):
    A = 1
    B = 2
    C = 4
    D = 8


class EV3Device(object):
    def __init__(self, socket):
        assert(socket is not None)
        self._socket = socket

    def __del__(self):
        """
        Just in case the last order was a move command, stop the robot.
        """
        self.stop()
        #self._socket.close()

    def get_move_command(self, motor, power, move=MoveOrder.POWER, safe=True):
        """
        `motor`: Motors enum (can be a sum)
        `power`: -31..31
        """
        str_motor = "{0:02d}".format(motor)
        if (safe):
            converted_power = max(-31, min(31, power))
            if (converted_power < 0):
                converted_power = 64 + converted_power
        else:
            converted_power = power
        str_power = hex(converted_power)[2:].zfill(2)
        if (MoveOrder.POWER == move):
            order = "A4"
        elif (MoveOrder.SPEED == move):
            order = "A5"
        else:
            order = "A4"
        command = "0C000000800000" + order + "00"\
            + str_motor + str_power + "A600" + str_motor
        return command.decode('hex')

    def get_stop_command(self, motor):
        """
        `motor`: Motors enum (can be a sum)
        """
        str_motor = "{0:02d}".format(motor)
        command = "09000000800000A300" + str_motor + "00"
        return command.decode('hex')

    def move(self, left, right):
        """
        `left`: -1..1
        `right`: -1..1
        """
        # 31 is a magic number comming from trial and error
        scaled_left = int(float(left) * float(31))
        scaled_right = int(float(right) * float(31))
        command = self.get_move_command(Motors.A.value, scaled_left)
        self._socket.send(command)
        command = self.get_move_command(Motors.D.value, scaled_right)
        self._socket.send(command)

    def stop(self):
        command = self.get_stop_command(Motors.A.value + Motors.D.value)
        self._socket.send(command)


class Program(object):
    def __init__(
            self,
            arguments,
            subscriber_type=Subscriber,
            pusher_type=Pusher):
        """
        `arguments`: object that must at least contain publisher_port,
            puller_port, address.
        `subscriber_type`: see #MessageHub
        `pusher_type`: see #MessageHub
        """
        self._message_hub = MessageHub(
            arguments.publisher_port,
            arguments.puller_port,
            arguments.address,
            subscriber_type,
            pusher_type)
        self._actionner = Actionner()
        self._robots = {}  # id -> Robot

    def add_robot(self, robot_id, device=None):
        """
        Create a rebot and ask it to register into the server.
        """
        robot = Robot(robot_id, self._message_hub, self._actionner, device)
        self._robots[robot_id] = robot
        robot.queue_register()

    @property
    def robots(self):
        return self._robots

    def step(self):
        """
        Run the actionner and the message hub (only one call).
        """
        self._actionner.step()
        self._message_hub.step()
        map(lambda robot: robot.step(), self._robots.itervalues())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-P", "--publisher-port",
        help="Publisher port (the server publish and we subscribe).",
        default=9001, type=int)
    parser.add_argument(
        "-p", "--puller-port",
        help="Puller port (the server pulls and we push).",
        default=9000, type=int)
    parser.add_argument(
        "--address",
        help="The server address",
        default="127.0.0.1", type=str)
    arguments = parser.parse_args()
    sockets_lister = SocketsLister()
    robots = ['951']
    program = Program(arguments)
    for robot in robots:
        socket = sockets_lister.pop_available_socket()
        if (socket):
            device = EV3Device(socket)
            program.add_robot(robot, device)
            print 'Device found for robot', robot
        else:
            print 'Oups, no device to associate to robot', robot
    while (True):
        program.step()

if ("__main__" == __name__):
    main()
