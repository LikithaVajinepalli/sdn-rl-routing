from routing.host_location import HostLocationTracker


def test_unknown_mac_returns_none():
    tracker = HostLocationTracker()
    assert tracker.get("aa:bb:cc:dd:ee:ff") is None
    assert tracker.dpid_of("aa:bb:cc:dd:ee:ff") is None


def test_record_and_get():
    tracker = HostLocationTracker()
    tracker.record("aa:bb:cc:dd:ee:ff", dpid=3, port_no=2)
    assert tracker.get("aa:bb:cc:dd:ee:ff") == (3, 2)
    assert tracker.dpid_of("aa:bb:cc:dd:ee:ff") == 3


def test_record_overwrites_previous_location():
    tracker = HostLocationTracker()
    tracker.record("mac1", dpid=3, port_no=2)
    tracker.record("mac1", dpid=5, port_no=1)  # host moved (or was relearned elsewhere)
    assert tracker.get("mac1") == (5, 1)
