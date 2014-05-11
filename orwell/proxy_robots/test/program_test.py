import orwell.proxy_robots.program as opp
import orwell.messages.server_game_pb2 as server_game_messages
#import orwell.messages.controller_pb2 as controller_messages
from nose.tools import assert_equals
from nose.tools import assert_true
from nose.tools import assert_false
from enum import Enum


INPUTS = [
    ('951', 'Grenade', 1.0, 0, False, False),
    ('951', 'Grenade', 0.5, -0.5, True, False)
]


class FakeDevice(object):
    def __init__(self, robot_id):
        self.expected_moves = []
        for other_internal_robot_id, other_robot_id,\
                left, right, fire1, fire2 in INPUTS:
            if (robot_id == other_robot_id):
                self.expected_moves.append((left, right))

    def move(
            self,
            left,
            right):
        expected_left, expected_right = self.expected_moves.pop(0)
        assert_equals(expected_left, left)
        assert_equals(expected_right, right)


ROBOT_DESCRIPTORS = [('951', 'Grenade', FakeDevice('Grenade'))]


class FakeArguments(object):
    publisher_port = 1
    puller_port = 2
    address = '1.2.3.4'


class MockPusher(object):
    def __init__(self, address, port, context):
        self.messages = []
        for robot_id, _, _ in ROBOT_DESCRIPTORS:
            message = opp.REGISTRY[opp.Messages.Register.name]()
            message.robot_id = robot_id
            payload = "{0} {1} {2}".format(
                robot_id,
                opp.Messages.Register.name,
                message.SerializeToString())
            self.messages.append(payload)

    def write(self, message):
        #print 'Fake writing message =', message
        expected_message = self.messages.pop(0)
        assert_equals(expected_message, message)


class MockSubscriber(object):
    def __init__(self, address, port, context):
        self.messages = [None]
        for robot_id, robot_name, _ in ROBOT_DESCRIPTORS:
            message = opp.REGISTRY[opp.Messages.Registered.name]()
            message.name = robot_name
            message.team = server_game_messages.BLU
            payload = "{0} {1} {2}".format(
                robot_id,
                opp.Messages.Registered.name,
                message.SerializeToString())
            self.messages.append(payload)
        for internal_robot_id, robot_id, left, right, fire1, fire2 in INPUTS:
            message = opp.REGISTRY[opp.Messages.Input.name]()
            message.move.left = left
            message.move.right = right
            message.fire.weapon1 = fire1
            message.fire.weapon2 = fire2
            payload = "{0} {1} {2}".format(
                robot_id,
                opp.Messages.Input.name,
                message.SerializeToString())
            self.messages.append(payload)

    def read(self):
        assert(self.messages)
        message = self.messages.pop(0)
        #print 'Fake reading message =', message
        return message


def test_robot_registration():
    print "\ntest_robot_registration"
    arguments = FakeArguments()
    program = opp.Program(arguments, MockSubscriber, MockPusher)
    for internal_robot_id, _, device in ROBOT_DESCRIPTORS:
        program.add_robot(internal_robot_id, device)
    program.step()
    program.step()
    for (internal_robot_id, robot), expected in zip(
            program.robots.items(),
            ROBOT_DESCRIPTORS):
        expected_robot_id, expected_robot_name, _ = expected
        assert_equals(expected_robot_name, robot.robot_id)
        assert_true(robot.registered)
    print "OK"
    check_simple_input(program)


def check_simple_input(program):
    for internal_robot_id, robot_id, _ in ROBOT_DESCRIPTORS:
        robot = program.robots[internal_robot_id]
        assert_equals(0.0, robot.left)
        assert_equals(0.0, robot.right)
        assert_false(robot.fire1)
        assert_false(robot.fire2)
    for internal_robot_id, robot_id, left, right, fire1, fire2 in INPUTS:
        program.step()
        robot = program.robots[internal_robot_id]
        assert_equals(left, robot.left)
        assert_equals(right, robot.right)
        assert_equals(fire1, robot.fire1)
        assert_equals(fire2, robot.fire2)
    for internal_robot_id, _, device in ROBOT_DESCRIPTORS:
        print 'internal_robot_id =', internal_robot_id
        assert_equals(len(device.expected_moves), 0)


INPUT_MOVE = (0.89, -0.5)


class DummyDevice(object):
    def __init__(self, robot_id):
        self._moved = False

    # it does not work
    #def __del__(self):
        #assert_true(self._moved)

    def move(
            self,
            left,
            right):
        print 'move', left, right
        assert_equals(INPUT_MOVE[0], left)
        assert_equals(INPUT_MOVE[1], right)
        self._moved = True


INPUT_ROBOT_DESCRIPTOR = ('55', 'Jambon', DummyDevice('55'))


class MockerStorage(object):
    def __init__(self, address, port, context):
        self.address = address
        self.port = port
        self.context = context


class Mocker(object):
    def __init__(self):
        self._pusher = None
        self._publisher = None

    def pusher_init_faker(self):
        def fake_init(address, port, context):
            self._pusher = MockerStorage(address, port, context)
            return self
        return fake_init

    def publisher_init_faker(self):
        def fake_init(address, port, context):
            self._publisher = MockerStorage(address, port, context)
            return self
        return fake_init


class InputMockerState(Enum):
    Created = 0
    Register = 1
    Registered = 2
    Input = 3


class InputMocker(Mocker):
    def __init__(self):
        super(Mocker, self).__init__()
        self._state = InputMockerState.Created
        self._internal_robot_id = INPUT_ROBOT_DESCRIPTOR[0]
        self._robot_id = INPUT_ROBOT_DESCRIPTOR[1]
        self._team = server_game_messages.BLU

    def read(self):
        print 'Fake read'
        payload = None
        if (InputMockerState.Register == self._state):
            message = opp.REGISTRY[opp.Messages.Registered.name]()
            message.name = self._robot_id
            message.team = self._team
            # here we need to reply with the internal_robot_id
            # which was used to initate the conversation
            payload = "{0} {1} {2}".format(
                self._internal_robot_id,
                opp.Messages.Registered.name,
                message.SerializeToString())
            print 'Fake message =', message
            self._state = InputMockerState.Input
        elif (InputMockerState.Input == self._state):
            message = opp.REGISTRY[opp.Messages.Input.name]()
            message.move.left = INPUT_MOVE[0]
            message.move.right = INPUT_MOVE[1]
            message.fire.weapon1 = False
            message.fire.weapon2 = False
            # here we have the definitive robot_id available
            payload = "{0} {1} {2}".format(
                self._robot_id,
                opp.Messages.Input.name,
                message.SerializeToString())
            print 'Fake message =', message
        return payload

    def write(self, payload):
        print 'Fake write'
        if (InputMockerState.Created == self._state):
            routing_id, message_type, raw_message = payload.split(' ', 2)
            assert_equals(opp.Messages.Register.name, message_type)
            message = opp.REGISTRY[message_type]()
            message.ParseFromString(raw_message)
            assert_equals(self._internal_robot_id, message.robot_id)
            self._state = InputMockerState.Register


def test_robot_input():
    print "\ntest_robot_input"
    arguments = FakeArguments()
    input_mocker = InputMocker()
    program = opp.Program(
        arguments,
        input_mocker.publisher_init_faker(),
        input_mocker.pusher_init_faker())
    internal_robot_id, robot_name, device = INPUT_ROBOT_DESCRIPTOR
    program.add_robot(internal_robot_id, device)
    program.step()
    program.step()
    program.step()
    #import time
    #time.sleep(1)


class FakeSocket(object):
    def __init__(self, expected_content_list):
        self._expected_content_list = expected_content_list

    def __del__(self):
        assert_equals(0, len(self._expected_content_list))

    #def close(self):
        #pass

    def send(self, content):
        expected_content = self._expected_content_list.pop(0)
        assert_equals(expected_content, content)


def test_fake_socket():
    robots = ['951']
    arguments = FakeArguments()
    program = opp.Program(arguments)
    for robot in robots:
        socket = FakeSocket(
            ['\x0c\x00\x00\x00\x80\x00\x00\xa4\x00\x01\x1f\xa6\x00\x01',
             '\x0c\x00\x00\x00\x80\x00\x00\xa4\x00\x08\x1f\xa6\x00\x08',
             '\t\x00\x00\x00\x80\x00\x00\xa3\x00\t\x00'])
        device = opp.EV3Device(socket)
        program.add_robot(robot, device)
        device.move(1, 1)


def main():
    test_robot_registration()
    test_robot_input()
    test_fake_socket()

if ("__main__" == __name__):
    main()
