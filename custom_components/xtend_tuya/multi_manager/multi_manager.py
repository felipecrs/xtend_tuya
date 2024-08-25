from __future__ import annotations
import requests
import copy
from typing import Any, Literal, Optional

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.entity import EntityDescription

from tuya_iot import (
    AuthType,
    TuyaOpenAPI,
)
from tuya_iot.device import (
    PROTOCOL_DEVICE_REPORT,
    PROTOCOL_OTHER,
)

from tuya_sharing.customerapi import (
    CustomerTokenInfo,
    CustomerApi,
)
from tuya_sharing.home import (
    HomeRepository,
)
from tuya_sharing.scenes import (
    SceneRepository,
)
from tuya_sharing.user import (
    UserRepository,
)

from ..const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    LOGGER,
    TUYA_CLIENT_ID,
    VirtualFunctions,
    DescriptionVirtualFunction,
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_APP_TYPE,
    CONF_AUTH_TYPE,
    CONF_COUNTRY_CODE,
    CONF_ENDPOINT_OT,
    CONF_PASSWORD,
    CONF_USERNAME,
    MESSAGE_SOURCE_TUYA_IOT,
    MESSAGE_SOURCE_TUYA_SHARING,
)

from .shared.import_stub import (
    MultiManager,
    XTConfigEntry,
)

from .shared.device import (
    XTDevice
)

from .shared.shared_classes import (
    DeviceWatcher,
    XTConfigEntry,  # noqa: F811
)

from .shared.multi_source_handler import (
    MultiSourceHandler,
)

from .shared.multi_mq import (
    MultiMQTTQueue,
)

from .shared.multi_device_listener import (
    MultiDeviceListener,
)

from .shared.multi_virtual_state_handler import (
    XTVirtualStateHandler,
)

from .shared.multi_virtual_function_handler import (
    XTVirtualFunctionHandler,
)

from ..util import (
    get_overriden_tuya_integration_runtime_data,
    prepare_value_for_property_update,
    merge_iterables,
    append_lists,
)

from .tuya_sharing.ha_tuya_integration.tuya_decorators import (
    decorate_tuya_manager,
)
from .tuya_sharing.ha_tuya_integration.config_entry_handler import (
    XTHATuyaIntegrationConfigEntryManager,
)

from .tuya_sharing.xt_tuya_sharing_data import (
    TuyaSharingData,
)

from .tuya_sharing.xt_tuya_sharing_token_listener import (
    XTSharingTokenListener,
)

from .tuya_sharing.xt_tuya_sharing_device_repository import (
    XTSharingDeviceRepository,
)

from .tuya_sharing.xt_tuya_sharing_manager import (
    XTSharingDeviceManager,
)
from .tuya_iot.xt_tuya_iot_data import (
    TuyaIOTData,
)
from .tuya_iot.xt_tuya_iot_manager import (
    XTIOTDeviceManager,
)
from .tuya_iot.xt_tuya_iot_mq import (
    XTIOTOpenMQ,
)
from .tuya_iot.xt_tuya_iot_home_manager import (
    XTIOTHomeManager,
)
    
class MultiManager:  # noqa: F811
    def __init__(self, hass: HomeAssistant) -> None:
        self.sharing_account: TuyaSharingData = None
        self.iot_account: TuyaIOTData = None
        self.virtual_state_handler = XTVirtualStateHandler(self)
        self.virtual_function_handler = XTVirtualFunctionHandler(self)
        self.descriptors_with_virtual_function = {}
        self.multi_mqtt_queue: MultiMQTTQueue = MultiMQTTQueue(self)
        self.multi_device_listener: MultiDeviceListener = MultiDeviceListener(hass, self)
        self.hass = hass
        self.multi_source_handler = MultiSourceHandler(self)
        self.device_watcher = DeviceWatcher()

    @property
    def device_map(self):
        return self.get_aggregated_device_map()
    
    @property
    def mq(self):
        return self.multi_mqtt_queue

    async def setup_entry(self, hass: HomeAssistant, config_entry: XTConfigEntry) -> None:
        self.sharing_account = await self.get_sharing_account(hass, config_entry)
        self.iot_account     = await self.get_iot_account(hass, config_entry)

    async def get_sharing_account(self, hass: HomeAssistant, config_entry: XTConfigEntry) -> TuyaSharingData | None:
        ha_tuya_integration_config_manager = XTHATuyaIntegrationConfigEntryManager(self, config_entry)
        #See if our current entry is an override of a Tuya integration entry
        tuya_integration_runtime_data = get_overriden_tuya_integration_runtime_data(hass, config_entry)
        reuse_config = False
        if tuya_integration_runtime_data:
            #We are using an override of the Tuya integration
            decorate_tuya_manager(tuya_integration_runtime_data.device_manager, ha_tuya_integration_config_manager)
            sharing_device_manager = XTSharingDeviceManager(multi_manager=self, other_device_manager=tuya_integration_runtime_data.device_manager)
            sharing_device_manager.terminal_id      = tuya_integration_runtime_data.device_manager.terminal_id
            sharing_device_manager.mq               = tuya_integration_runtime_data.device_manager.mq
            sharing_device_manager.customer_api     = tuya_integration_runtime_data.device_manager.customer_api
            tuya_integration_runtime_data.device_manager.device_listeners.clear()
            #self.convert_tuya_devices_to_xt(tuya_integration_runtime_data.device_manager)
            reuse_config = True
        else:
            #We are using XT as a standalone integration
            sharing_device_manager = XTSharingDeviceManager(multi_manager=self, other_device_manager=None)
            token_listener = XTSharingTokenListener(hass, config_entry)
            sharing_device_manager.terminal_id = config_entry.data[CONF_TERMINAL_ID]
            sharing_device_manager.customer_api = CustomerApi(
                CustomerTokenInfo(config_entry.data[CONF_TOKEN_INFO]),
                TUYA_CLIENT_ID,
                config_entry.data[CONF_USER_CODE],
                config_entry.data[CONF_ENDPOINT],
                token_listener,
            )
            sharing_device_manager.mq = None
        sharing_device_manager.home_repository = HomeRepository(sharing_device_manager.customer_api)
        sharing_device_manager.device_repository = XTSharingDeviceRepository(sharing_device_manager.customer_api, sharing_device_manager, self)
        sharing_device_manager.scene_repository = SceneRepository(sharing_device_manager.customer_api)
        sharing_device_manager.user_repository = UserRepository(sharing_device_manager.customer_api)
        sharing_device_manager.add_device_listener(self.multi_device_listener)
        return TuyaSharingData(
            device_manager=sharing_device_manager, 
            device_ids=[], 
            ha_tuya_integration_config_manager=ha_tuya_integration_config_manager, 
            reuse_config=reuse_config
            )

    async def get_iot_account(self, hass: HomeAssistant, entry: XTConfigEntry) -> TuyaIOTData | None:
        if (
            entry.options is None
            or CONF_AUTH_TYPE     not in entry.options
            or CONF_ENDPOINT_OT   not in entry.options
            or CONF_ACCESS_ID     not in entry.options
            or CONF_ACCESS_SECRET not in entry.options
            or CONF_USERNAME      not in entry.options
            or CONF_PASSWORD      not in entry.options
            or CONF_COUNTRY_CODE  not in entry.options
            or CONF_APP_TYPE      not in entry.options
            ):
            return None
        auth_type = AuthType(entry.options[CONF_AUTH_TYPE])
        api = TuyaOpenAPI(
            endpoint=entry.options[CONF_ENDPOINT_OT],
            access_id=entry.options[CONF_ACCESS_ID],
            access_secret=entry.options[CONF_ACCESS_SECRET],
            auth_type=auth_type,
        )
        api.set_dev_channel("hass")
        try:
            if auth_type == AuthType.CUSTOM:
                response = await hass.async_add_executor_job(
                    api.connect, entry.options[CONF_USERNAME], entry.options[CONF_PASSWORD]
                )
            else:
                response = await hass.async_add_executor_job(
                    api.connect,
                    entry.options[CONF_USERNAME],
                    entry.options[CONF_PASSWORD],
                    entry.options[CONF_COUNTRY_CODE],
                    entry.options[CONF_APP_TYPE],
                )
        except requests.exceptions.RequestException as err:
            raise ConfigEntryNotReady(err) from err

        if response.get("success", False) is False:
            raise ConfigEntryNotReady(response)
        mq = XTIOTOpenMQ(api)
        mq.start()
        device_manager = XTIOTDeviceManager(self, api, mq)
        device_ids: list[str] = list()
        home_manager = XTIOTHomeManager(api, mq, device_manager, self)
        device_manager.add_device_listener(self.multi_device_listener)
        return TuyaIOTData(
            device_manager=device_manager,
            mq=mq,
            device_ids=device_ids,
            home_manager=home_manager)
    
    def update_device_cache(self):
        if self.sharing_account:
            self.sharing_account.device_manager.update_device_cache()
            new_device_ids: list[str] = [device_id for device_id in self.sharing_account.device_manager.device_map]
            self.sharing_account.device_ids.clear()
            self.sharing_account.device_ids.extend(new_device_ids)
        if self.iot_account:
            self.iot_account.home_manager.update_device_cache()
            new_device_ids: list[str] = [device_id for device_id in self.iot_account.device_manager.device_map]
            self.iot_account.device_ids.clear()
            self.iot_account.device_ids.extend(new_device_ids)
        self._merge_devices_from_multiple_sources()
    
    def convert_tuya_devices_to_xt(self, manager):
        for dev_id in manager.device_map:
            manager.device_map[dev_id] = XTDevice.from_compatible_device(manager.device_map[dev_id])

    def _get_available_device_maps(self) -> list[dict[str, XTDevice]]:
        return_list: list[dict[str, XTDevice]] = list()
        if self.sharing_account:
            if other_manager := self.sharing_account.device_manager.get_overriden_device_manager():
                return_list.append(other_manager.device_map)
            return_list.append(self.sharing_account.device_manager.device_map)
        if self.iot_account:
            return_list.append(self.iot_account.device_manager.device_map)
        return return_list

    def _merge_devices_from_multiple_sources(self):
        #Merge the device function, status_range and status between managers
        device_maps = self._get_available_device_maps()
        aggregated_device_list = self.device_map
        for device in aggregated_device_list.values():
            to_be_merged: list[XTDevice] = []
            devices = self.get_devices_from_device_id(device.id)
            for current_device in devices:
                for prev_device in to_be_merged:
                    self._merge_devices(current_device, prev_device)
                to_be_merged.append(current_device)
        for device_map in device_maps:
            merge_iterables(device_map, aggregated_device_list)
        
    def _merge_devices(self, receiving_device: XTDevice, giving_device: XTDevice):
        merge_iterables(receiving_device.status_range, giving_device.status_range)
        merge_iterables(receiving_device.function, giving_device.function)
        merge_iterables(receiving_device.status, giving_device.status)
        if hasattr(receiving_device, "local_strategy") and hasattr(giving_device, "local_strategy"):
            merge_iterables(receiving_device.local_strategy, giving_device.local_strategy)
        if hasattr(receiving_device, "data_model") and hasattr(giving_device, "data_model"):
            if receiving_device.data_model == "" and giving_device.data_model != "":
                receiving_device.data_model = copy.deepcopy(giving_device.data_model)
            if giving_device.data_model == "" and receiving_device.data_model != "":
                giving_device.data_model = copy.deepcopy(receiving_device.data_model)
    
    def get_aggregated_device_map(self) -> dict[str, XTDevice]:
        aggregated_list: dict[str, XTDevice] = {}
        device_maps = self._get_available_device_maps()
        for device_map in device_maps:
            for device_id in device_map:
                if device_id not in aggregated_list:
                    aggregated_list[device_id] = device_map[device_id]
        return aggregated_list
    
    def unload(self):
        if self.sharing_account and not self.iot_account:
            #Only call the unload of the Sharing Manager if there is no IOT account as this will revoke its credentials
            self.sharing_account.device_manager.user_repository.unload(self.sharing_account.device_manager.terminal_id)
    
    def refresh_mq(self):
        if self.sharing_account:
            self.sharing_account.device_manager.refresh_mq()

    def register_device_descriptors(self, name: str, descriptors):
        self.virtual_state_handler.register_device_descriptors(name, descriptors)
        self.virtual_function_handler.register_device_descriptors(name, descriptors)
    
    def remove_device_listeners(self) -> None:
        if self.iot_account:
            self.iot_account.device_manager.remove_device_listener(self.multi_device_listener)
        if self.sharing_account:
            self.sharing_account.device_manager.remove_device_listener(self.multi_device_listener)
    
    def _read_dpId_from_code(self, code: str, device: XTDevice) -> int | None:
        if not hasattr(device, "local_strategy"):
            return None
        for dpId in device.local_strategy:
            if device.local_strategy[dpId]["status_code"] == code:
                return dpId
        return None
    
    def _read_code_from_dpId(self, dpId: int, device: XTDevice) -> str | None:
        if dp_id_item := device.local_strategy.get(dpId, None):
            return dp_id_item["status_code"]
        return None

    def allow_virtual_devices_not_set_up(self, device: XTDevice):
        if not device.id.startswith("vdevo"):
            return
        if not getattr(device, "set_up", True):
            setattr(device, "set_up", True)
    
    def get_devices_from_device_id(self, device_id: str) -> list[XTDevice] | None:
        return_list = []
        device_maps = self._get_available_device_maps()
        for device_map in device_maps:
            if device_id in device_map:
                return_list.append(device_map[device_id])
        return return_list

    def _read_code_dpid_value_from_state(self, device_id: str, state, fail_if_dpid_not_found = True, fail_if_code_not_found = True):
        devices = self.get_devices_from_device_id(device_id)
        code = None
        dpId = None
        value = None
        if "value" in state:
            value = state["value"]
        for device in devices:
            if code is None and "code" in state:
                code = state["code"]
            if dpId is None and "dpId" in state:
                dpId = state["dpId"]

            if code is None and dpId is not None:
                code = self._read_code_from_dpId(dpId, device)

            if dpId is None and code is not None:
                dpId = self._read_dpId_from_code(code, device)

            if dpId is None and code is None:
                for temp_dpId in state:
                    temp_code = self._read_code_from_dpId(int(temp_dpId), device)
                    if temp_code is not None:
                        dpId = int(temp_dpId)
                        code = temp_code
                        value = state[temp_dpId]

            if code is not None and dpId is not None:
                return code, dpId, value, True
        if code is None and fail_if_code_not_found:
            LOGGER.warning(f"_read_code_value_from_state FAILED => {device.id} <=> {device.name} <=> {state} <=> {device.local_strategy}")
            return None, None, None, False
        if dpId is None and fail_if_dpid_not_found:
            LOGGER.warning(f"_read_code_value_from_state FAILED => {device.id} <=> {device.name} <=> {state} <=> {device.local_strategy}")
            return None, None, None, False
        return code, dpId, value, True

    def convert_device_report_status_list(self, device_id: str, status_in: list) -> list:
        status = copy.deepcopy(status_in)
        for item in status:
            code, dpId, value, result_ok = self._read_code_dpid_value_from_state(device_id, item)
            if result_ok:
                item["code"] = code
                item["dpId"] = dpId
                item["value"] = value
            else:
                LOGGER.warning(f"convert_device_report_status_list code retrieval failed => {item} <=>{device_id}")
        return status

    def on_message_from_tuya_iot(self, msg:str):
        self.on_message(MESSAGE_SOURCE_TUYA_IOT, msg)
    
    def on_message_from_tuya_sharing(self, msg:str):
        self.on_message(MESSAGE_SOURCE_TUYA_SHARING, msg)

    def on_message(self, source: str, msg: str):
        dev_id = self._get_device_id_from_message(msg)
        if not dev_id:
            LOGGER.warning(f"dev_id {dev_id} not found!")
            return
        
        if self.device_watcher.is_watched(dev_id):
            LOGGER.warning(f"WD: on_message => {msg}")

        new_message = self._convert_message_for_all_accounts(msg)
        if status_list := self._get_status_list_from_message(msg):
            self.multi_source_handler.register_status_list_from_source(dev_id, source, status_list)
            if self.device_watcher.is_watched(dev_id):
                LOGGER.warning(f"WD: on_message status list => {status_list}")
        
        if self.sharing_account and source == MESSAGE_SOURCE_TUYA_SHARING:
            self.sharing_account.device_manager.on_message(new_message)
        if self.iot_account and source == MESSAGE_SOURCE_TUYA_IOT:
            self.iot_account.device_manager.on_message(new_message)

    def _get_device_id_from_message(self, msg: str) -> str | None:
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if (dev_id := data.get("devId", None)):
            return dev_id
        if protocol == PROTOCOL_OTHER:
            if bizData := data.get("bizData", None):
                if dev_id := bizData.get("devId", None):
                    return dev_id
        return None
    
    def _get_status_list_from_message(self, msg: str) -> str | None:
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if protocol == PROTOCOL_DEVICE_REPORT and "status" in data:
            return data["status"]
        return None

    def _convert_message_for_all_accounts(self, msg: str) -> str:
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if protocol == PROTOCOL_DEVICE_REPORT:
            return msg
        elif protocol == PROTOCOL_OTHER:
            if hasattr(data, "devId"):
                return msg
            else:
                if bizData := data.get("bizData", None):
                    if dev_id := bizData.get("devId", None):
                        data["devId"] = dev_id
        return msg

    def query_scenes(self) -> list:
        return_list = []
        if self.sharing_account:
            temp_list = self.sharing_account.device_manager.query_scenes()
            return_list = append_lists(return_list, temp_list)
        if self.iot_account:
            temp_list = self.iot_account.home_manager.query_scenes()
            return_list = append_lists(return_list, temp_list)
        return return_list

    def send_commands(
            self, device_id: str, commands: list[dict[str, Any]]
    ):
        open_api_regular_commands: list[dict[str, Any]] = []
        regular_commands: list[dict[str, Any]] = []
        property_commands: list[dict[str, Any]] = []
        virtual_function_commands: list[dict[str, Any]] = []
        device_map = self.get_aggregated_device_map()
        if device := device_map.get(device_id, None):
            virtual_function_list = self.virtual_function_handler.get_category_virtual_functions(device.category)
            for command in commands:
                command_code  = command["code"]
                command_value = command["value"]
                LOGGER.debug(f"Base command : {command}")
                vf_found = False
                for virtual_function in virtual_function_list:
                    if (command_code == virtual_function.key or
                        command_code in virtual_function.vf_reset_state):
                        command_dict = {"code": command_code, "value": command_value, "virtual_function": virtual_function}
                        virtual_function_commands.append(command_dict)
                        vf_found = True
                        break
                if vf_found:
                    continue
                for dp_item in device.local_strategy.values():
                    dp_item_code = dp_item.get("status_code", None)
                    if command_code == dp_item_code:
                        if not dp_item.get("use_open_api", False):
                            #command_dict = {"code": code, "value": value}
                            regular_commands.append(command)
                        else:
                            if dp_item.get("property_update", False):
                                command_value = prepare_value_for_property_update(dp_item, command_value)
                                property_dict = {str(dp_item_code): command_value}
                                property_commands.append(property_dict)
                            else:
                                command_dict = {"code": dp_item_code, "value": command_value}
                                open_api_regular_commands.append(command_dict)
                            break
            if virtual_function_commands:
                LOGGER.debug(f"Sending virtual function command : {virtual_function_commands}")
                self.virtual_function_handler._process_virtual_function(device_id, virtual_function_commands)
            if regular_commands:
                LOGGER.debug(f"Sending regular command : {regular_commands}")
                self.sharing_account.device_manager.send_commands(device_id, regular_commands)
            if open_api_regular_commands:
                LOGGER.debug(f"Sending Open API regular command : {open_api_regular_commands}")
                self.iot_account.device_manager.send_commands(device_id, open_api_regular_commands)
            if property_commands:
                LOGGER.debug(f"Sending property command : {property_commands}")
                self.iot_account.device_manager.send_property_update(device_id, property_commands)
            return
        self.sharing_account.device_manager.send_commands(device_id, commands)

    def get_device_stream_allocate(
            self, device_id: str, stream_type: Literal["flv", "hls", "rtmp", "rtsp"]
    ) -> Optional[str]:
        if self.sharing_account and device_id in self.sharing_account.device_ids:
            return self.sharing_account.device_manager.get_device_stream_allocate(device_id, stream_type)
        if self.iot_account and device_id in self.iot_account.device_ids:
            return self.iot_account.device_manager.get_device_stream_allocate(device_id, stream_type)