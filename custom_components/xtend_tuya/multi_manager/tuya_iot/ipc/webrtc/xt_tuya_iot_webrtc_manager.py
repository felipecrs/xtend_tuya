from __future__ import annotations

from datetime import datetime, timedelta
import time
import json

from .....const import (
    LOGGER,  # noqa: F401
)
from ..xt_tuya_iot_ipc_manager import (
    XTIOTIPCManager,
)

class XTIOTWebRTCContent:
    webrtc_config: dict[str, any]
    offer: str
    answer: str
    candidates: list[dict]
    has_all_candidates: bool

    def __init__(self, ttl: int = 10) -> None:
        self.webrtc_config = {}
        self.answer = None
        self.offer = None
        self.candidates = []
        self.valid_until = datetime.now() + timedelta(0, ttl)
        self.has_all_candidates = False

class XTIOTWebRTCManager:
    def __init__(self, ipc_manager: XTIOTIPCManager) -> None:
        self.sdp_exchange: dict[str, XTIOTWebRTCContent] = {}
        self.ipc_manager = ipc_manager
    
    def get_sdp_exchange(self, session_id: str) -> XTIOTWebRTCContent | None:
        self._clean_cache()
        if result := self.sdp_exchange.get(session_id):
            return result
        return None
    
    def _clean_cache(self) -> None:
        current_time = datetime.now()
        for session_id in self.sdp_exchange:
            if self.sdp_exchange[session_id].valid_until < current_time:
                self.sdp_exchange.pop(session_id)
    
    def set_sdp_answer(self, session_id: str, answer: str) -> None:
        self._create_session_if_necessary(session_id)
        self.sdp_exchange[session_id].answer = answer
    
    def add_sdp_candidate(self, session_id: str, candidate: dict) -> None:
        self._create_session_if_necessary(session_id)
        self.sdp_exchange[session_id].candidates.append(candidate)
        candidate_str = candidate.get("candidate", None)
        if candidate_str == '':
            self.sdp_exchange[session_id].has_all_candidates = True

    def set_webrtc_config(self, session_id: str, config: dict[str, any]):
        self._create_session_if_necessary(session_id)

        #Format ICE Servers so that they can be used by GO2RTC
        p2p_config: dict = config.get("p2p_config", {})
        if ices := p2p_config.get("ices"):
            p2p_config["ices"] = json.dumps(ices).replace(': ', ':').replace(', ', ',')
        self.sdp_exchange[session_id].webrtc_config = config

    def set_sdp_offer(self, session_id: str, offer: str) -> None:
        self._create_session_if_necessary(session_id)
        self.sdp_exchange[session_id].offer = offer

    def _create_session_if_necessary(self, session_id: str) -> None:
        if session_id not in self.sdp_exchange:
            self.sdp_exchange[session_id] = XTIOTWebRTCContent()
    
    def get_webrtc_config(self, device_id: str, session_id: str) -> dict | None:
        if current_exchange := self.get_sdp_exchange(session_id):
            if current_exchange.webrtc_config:
                return current_exchange.webrtc_config
        
        webrtc_config = self.ipc_manager.api.get(f"/v1.0/devices/{device_id}/webrtc-configs")
        if webrtc_config.get("success"):
            result = webrtc_config.get("result")
            self.set_webrtc_config(session_id, result)
            return result
        return None
    
    def get_webrtc_ice_servers(self, device_id: str, session_id: str) -> None:
        if config := self.get_webrtc_config(device_id, session_id):
            p2p_config: dict = config.get("p2p_config", {})
            return p2p_config.get("ices", None)

    def get_sdp_answer(self, device_id: str, session_id: str, sdp_offer: str, wait_for_answers: int = 5) -> str | None:
        sleep_step = 0.01
        sleep_count: int = int(wait_for_answers / sleep_step)
        self.set_sdp_offer(session_id, sdp_offer)
        if webrtc_config := self.get_webrtc_config(device_id, session_id):
            auth_token = webrtc_config.get("auth")
            moto_id =  webrtc_config.get("moto_id")
            topic: str = None
            for topic in self.ipc_manager.ipc_mq.mq_config.sink_topic.values():
                topic = topic.replace("{device_id}", device_id)
                topic = topic.replace("moto_id", moto_id)
                payload = {
                    "protocol":302,
                    "pv":"2.2",
                    "t":int(time.time()),
                    "data":{
                        "header":{
                            "type":"offer",
                            "from":f"{self.ipc_manager.get_from()}",
                            "to":f"{device_id}",
                            "sub_dev_id":"",
                            "sessionid":f"{session_id}",
                            "moto_id":f"{moto_id}",
                            "tid":""
                        },
                        "msg":{
                            "mode":"webrtc",
                            "sdp":f"{sdp_offer}",
                            "stream_type":1,
                            "auth":f"{auth_token}",
                        }
                    },
                }
                self.ipc_manager._publish_to_ipc_mqtt(topic, json.dumps(payload))
                for _ in range(sleep_count):
                    if answer := self.get_sdp_exchange(session_id):
                        if answer.has_all_candidates:
                            break
                    time.sleep(sleep_step) #Wait for MQTT responses
                if answer := self.get_sdp_exchange(session_id):
                    #Format SDP answer and send it back
                    sdp_answer: str = answer.answer.get("sdp", "")
                    candidates: str = ""
                    for candidate in answer.candidates:
                        candidates += candidate.get("candidate", "")
                    sdp_answer += candidates
                    return sdp_answer
            
            if not auth_token or not moto_id:
                return None
            
        return None
    