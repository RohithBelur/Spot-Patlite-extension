# robot_command_mission_service.py

import argparse
import logging
import os
import random
import string
import sys
import threading

import bosdyn.client
import bosdyn.client.util
from bosdyn.api import header_pb2
from bosdyn.api.mission import remote_pb2, remote_service_pb2_grpc
from bosdyn.client import time_sync
from bosdyn.client.directory_registration import (
    DirectoryRegistrationClient,
    DirectoryRegistrationKeepAlive,
)
from bosdyn.client.lease import Lease, LeaseClient
from bosdyn.client.server_util import GrpcServiceRunner, ResponseContext
from bosdyn.client.service_customization_helpers import (
    create_value_validator,
    make_dict_child_spec,
    make_dict_param_spec,
    make_string_param_spec,
    make_user_interface_info,
    validate_dict_spec,
)
from bosdyn.client.util import setup_logging

from main import (
    BUZZER_NAME_TO_ID,
    COLOR_NAME_TO_ID,
    PITCH_NAME_TO_ID,
    STATE_NAME_TO_ID,
    PatliteCommand,
    PatliteController,
)


DIRECTORY_NAME = "patlite-remote-service"
AUTHORITY = "remote-mission"
SERVICE_TYPE = "bosdyn.api.mission.RemoteMissionService"

_LOGGER = logging.getLogger(__name__)

_COMMAND_KEY = "command"
_COLOR_KEY = "color"
_STATE_KEY = "state"
_RED_KEY = "red"
_YELLOW_KEY = "yellow"
_GREEN_KEY = "green"
_PATTERN_KEY = "pattern"
_LIMIT_KEY = "limit"
_PITCH1_KEY = "pitch1"
_PITCH2_KEY = "pitch2"


def _enum_param(options, default_value, display_name, description):
    spec = make_string_param_spec(
        options=list(options),
        default_value=default_value,
        editable=True,
    )
    ui = make_user_interface_info(display_name, description)
    return make_dict_child_spec(spec, ui)


class PatliteRemoteMissionServicer(
    remote_service_pb2_grpc.RemoteMissionServiceServicer
):
    RESOURCE = "body"

    def __init__(self, bosdyn_sdk_robot, controller: PatliteController, logger=None):
        self.lock = threading.Lock()
        self.logger = logger or _LOGGER
        self.bosdyn_sdk_robot = bosdyn_sdk_robot
        self.controller = controller
        self.sessions_by_id = {}
        self._used_session_ids = []

        command_options = ["noop", "off", "single", "tower", "buzzer", "buzzer_ex"]
        limit_options = [str(value) for value in range(16)]
        dict_spec = make_dict_param_spec(
            {
                _COMMAND_KEY: _enum_param(
                    command_options,
                    "noop",
                    "Patlite Command",
                    "Select the Patlite action to run.",
                ),
                _COLOR_KEY: _enum_param(
                    COLOR_NAME_TO_ID.keys(),
                    "red",
                    "Single Color",
                    "Color used when command=single.",
                ),
                _STATE_KEY: _enum_param(
                    STATE_NAME_TO_ID.keys(),
                    "on",
                    "Single State",
                    "Light state used when command=single.",
                ),
                _RED_KEY: _enum_param(
                    STATE_NAME_TO_ID.keys(),
                    "off",
                    "Red State",
                    "Red segment state when command=tower.",
                ),
                _YELLOW_KEY: _enum_param(
                    STATE_NAME_TO_ID.keys(),
                    "off",
                    "Yellow State",
                    "Yellow segment state when command=tower.",
                ),
                _GREEN_KEY: _enum_param(
                    STATE_NAME_TO_ID.keys(),
                    "off",
                    "Green State",
                    "Green segment state when command=tower.",
                ),
                _PATTERN_KEY: _enum_param(
                    BUZZER_NAME_TO_ID.keys(),
                    "off",
                    "Buzzer Pattern",
                    "Buzzer pattern for buzzer commands.",
                ),
                _LIMIT_KEY: _enum_param(
                    limit_options,
                    "0",
                    "Buzzer Limit",
                    "0 is continuous, 1-15 follows the Patlite protocol repeat behavior.",
                ),
                _PITCH1_KEY: _enum_param(
                    PITCH_NAME_TO_ID.keys(),
                    "default_a",
                    "Pitch 1",
                    "First buzzer pitch for command=buzzer_ex.",
                ),
                _PITCH2_KEY: _enum_param(
                    PITCH_NAME_TO_ID.keys(),
                    "default_b",
                    "Pitch 2",
                    "Second buzzer pitch for command=buzzer_ex.",
                ),
            },
            is_hidden_by_default=False,
        )
        validate_dict_spec(dict_spec)
        self.custom_params = dict_spec

    def _get_unique_random_session_id(self):
        while True:
            sid = "".join(random.choice(string.ascii_letters) for _ in range(16))
            if sid not in self._used_session_ids:
                return sid

    def _sublease_or_none(self, leases, response, error_code):
        matches = [lease for lease in leases if lease.resource == self.RESOURCE]
        if len(matches) == 1:
            provided_lease = Lease(matches[0])
            return provided_lease.create_sublease()
        if not matches:
            response.status = error_code
            response.missing_lease_resources.append(self.RESOURCE)
            return None
        response.header.error.code = header_pb2.CommonError.CODE_INVALID_REQUEST
        response.header.error.message = (
            f"{len(matches)} leases on resource {self.RESOURCE}"
        )
        return None

    def _param_value(self, params, key, default):
        if key not in params.values:
            return default
        if params.values[key].WhichOneof("value") != "string_value":
            return default
        return params.values[key].string_value.value

    def _build_command(self, params) -> PatliteCommand:
        command_name = self._param_value(params, _COMMAND_KEY, "noop")
        if command_name not in {"noop", "off", "single", "tower", "buzzer", "buzzer_ex"}:
            raise ValueError(f"unsupported command '{command_name}'")

        limit_text = self._param_value(params, _LIMIT_KEY, "0")
        limit = int(limit_text)
        if not 0 <= limit <= 15:
            raise ValueError("limit must be between 0 and 15")

        return PatliteCommand(
            command=command_name,
            color=self._param_value(params, _COLOR_KEY, "red"),
            state=self._param_value(params, _STATE_KEY, "on"),
            red=self._param_value(params, _RED_KEY, "off"),
            yellow=self._param_value(params, _YELLOW_KEY, "off"),
            green=self._param_value(params, _GREEN_KEY, "off"),
            pattern=self._param_value(params, _PATTERN_KEY, "off"),
            limit=limit,
            pitch1=self._param_value(params, _PITCH1_KEY, "default_a"),
            pitch2=self._param_value(params, _PITCH2_KEY, "default_b"),
        )

    def EstablishSession(self, request, context):
        response = remote_pb2.EstablishSessionResponse()
        with ResponseContext(response, request):
            with self.lock:
                sublease = self._sublease_or_none(
                    request.leases,
                    response,
                    remote_pb2.EstablishSessionResponse.STATUS_MISSING_LEASES,
                )
                if sublease is None:
                    return response
                try:
                    self.bosdyn_sdk_robot.time_sync.wait_for_sync()
                except time_sync.TimedOutError:
                    response.header.error.code = (
                        header_pb2.CommonError.CODE_INTERNAL_SERVER_ERROR
                    )
                    response.header.error.message = "Failed to time sync with robot"
                    return response
                sid = self._get_unique_random_session_id()
                self.sessions_by_id[sid] = {}
                self._used_session_ids.append(sid)
                response.session_id = sid
                response.status = remote_pb2.EstablishSessionResponse.STATUS_OK
        return response

    def GetRemoteMissionServiceInfo(self, request, context):
        response = remote_pb2.GetRemoteMissionServiceInfoResponse()
        with ResponseContext(response, request):
            response.custom_params.CopyFrom(self.custom_params)
        return response

    def Tick(self, request, context):
        response = remote_pb2.TickResponse()
        with ResponseContext(response, request):
            with self.lock:
                if request.session_id not in self.sessions_by_id:
                    response.status = remote_pb2.TickResponse.STATUS_INVALID_SESSION_ID
                    return response

                sublease = self._sublease_or_none(
                    request.leases,
                    response,
                    remote_pb2.TickResponse.STATUS_MISSING_LEASES,
                )
                if sublease is None:
                    return response

                valid = create_value_validator(self.custom_params)(request.params)
                if valid is not None:
                    response.status = remote_pb2.TickResponse.STATUS_CUSTOM_PARAMS_ERROR
                    response.custom_param_error.CopyFrom(valid)
                    return response

                lease_client = self.bosdyn_sdk_robot.ensure_client(
                    LeaseClient.default_service_name
                )
                lease_client.retain_lease_async(sublease)

                try:
                    command = self._build_command(request.params)
                    if command.command == "noop":
                        response.status = remote_pb2.TickResponse.STATUS_RUNNING
                        return response

                    ok = self.controller.execute(command)
                    if not ok:
                        raise RuntimeError("Patlite command did not complete successfully")

                    self.logger.info("Executed Patlite command '%s'", command.command)
                    response.status = remote_pb2.TickResponse.STATUS_SUCCESS
                except Exception as exc:
                    self.logger.exception("Patlite command failed")
                    response.status = remote_pb2.TickResponse.STATUS_FAILURE
                    response.header.error.code = (
                        header_pb2.CommonError.CODE_INTERNAL_SERVER_ERROR
                    )
                    response.header.error.message = str(exc)
        return response

    def Stop(self, request, context):
        response = remote_pb2.StopResponse()
        with ResponseContext(response, request):
            response.status = remote_pb2.StopResponse.STATUS_OK
        return response

    def TeardownSession(self, request, context):
        response = remote_pb2.TeardownSessionResponse()
        with ResponseContext(response, request):
            with self.lock:
                if request.session_id in self.sessions_by_id:
                    del self.sessions_by_id[request.session_id]
                    response.status = remote_pb2.TeardownSessionResponse.STATUS_OK
                else:
                    response.status = (
                        remote_pb2.TeardownSessionResponse.STATUS_INVALID_SESSION_ID
                    )
        return response


def run_service(bosdyn_sdk_robot, port, controller: PatliteController, logger=None):
    service_servicer = PatliteRemoteMissionServicer(
        bosdyn_sdk_robot, controller, logger=logger
    )
    return GrpcServiceRunner(
        service_servicer,
        remote_service_pb2_grpc.add_RemoteMissionServiceServicer_to_server,
        port,
        logger=logger,
    )


def main():
    if len(sys.argv) == 1:
        env_host = os.environ.get("SPOT_HOSTNAME") or os.environ.get("BOSDYN_HOSTNAME")
        if env_host:
            sys.argv.append(env_host)

    parser = argparse.ArgumentParser()
    bosdyn.client.util.add_base_arguments(parser)
    bosdyn.client.util.add_service_endpoint_arguments(parser)
    options = parser.parse_args()

    setup_logging(options.verbose)

    sdk = bosdyn.client.create_standard_sdk("PatliteRemoteMissionSDK")
    robot = sdk.create_robot(options.hostname)

    user = os.environ.get("SPOT_USERNAME") or os.environ.get("BOSDYN_CLIENT_USERNAME")
    pw = os.environ.get("SPOT_PASSWORD") or os.environ.get("BOSDYN_CLIENT_PASSWORD")

    if user and pw:
        robot.authenticate(user, pw)
    else:
        raise RuntimeError(
            "Set SPOT_USERNAME/SPOT_PASSWORD "
            "(or BOSDYN_CLIENT_USERNAME/BOSDYN_CLIENT_PASSWORD) in the environment."
        )

    controller = PatliteController()
    service_runner = run_service(robot, options.port, controller=controller, logger=_LOGGER)

    dir_reg_client = robot.ensure_client(
        DirectoryRegistrationClient.default_service_name
    )
    keep_alive = DirectoryRegistrationKeepAlive(dir_reg_client, logger=_LOGGER)
    keep_alive.start(
        DIRECTORY_NAME,
        SERVICE_TYPE,
        AUTHORITY,
        options.host_ip,
        service_runner.port,
    )

    with keep_alive:
        service_runner.run_until_interrupt()


if __name__ == "__main__":
    main()
