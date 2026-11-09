from config import Settings


def make(**kw):
    return Settings(_env_file=None, **kw)


def test_access_open_when_no_token():
    assert make().check_access({"sdp": "x", "type": "offer"})


def test_access_requires_matching_token():
    s = make(access_token="s3cret")
    assert not s.check_access({"sdp": "x"})
    assert not s.check_access({"request_data": {"access_token": "nope"}})
    assert s.check_access({"request_data": {"access_token": "s3cret"}})
    assert s.check_access({"requestData": {"access_token": "s3cret"}})


def test_ice_servers_and_client_config():
    assert make().ice_servers() == []
    s = make(stun_url="stun:a:1", turn_url="turn:b:443", turn_username="u", turn_credential="c",
             wait_for_ice_gathering=True)
    assert s.ice_servers() == [{"urls": "stun:a:1"}, {"urls": "turn:b:443", "username": "u", "credential": "c"}]
    assert s.client_rtc_config()["waitForIceGathering"] is True
