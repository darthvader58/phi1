"""The replica holding a client's socket is not necessarily the one that
enqueued the match, and it is never the process that ran it.

So "this match finished" has to travel between processes. Pub/sub is the right
shape because delivery is to every subscriber (each replica has its own
sockets to serve) rather than to one consumer.
"""

import uuid

import pytest

from backend.jobs.events import CHANNEL, MatchEvents
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


@pytest.fixture
def events():
    """A fresh, uniquely-named channel per test.

    `CHANNEL` is one fixed name shared by every worker and API replica in
    production, which is the point of a constant — they have to agree on it.
    But that same fixed name means two copies of this file running at once
    (this Redis is shared with other processes on the machine, and CI can run
    suites concurrently) would subscribe to and publish on the same channel
    and cross-deliver each other's events: a "nothing was published" check in
    one run would see the other run's publish, and a "here is the message I
    just sent" check would see a message the wrong run sent. A random channel
    per test isolates each test from every other test and every other
    process, while leaving the production CHANNEL untouched.
    """
    return MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")


def test_a_subscriber_receives_a_published_event(events):
    pubsub = events.subscribe()
    try:
        events.publish({"type": "match_finished", "match_id": "m_1"})
        received = events.listen(pubsub, timeout=2.0)
        assert received == {"type": "match_finished", "match_id": "m_1"}
    finally:
        pubsub.close()


def test_listen_returns_none_when_nothing_is_published(events):
    pubsub = events.subscribe()
    try:
        assert events.listen(pubsub, timeout=0.3) is None
    finally:
        pubsub.close()


def test_every_subscriber_receives_the_same_event(events):
    """Both replicas must learn a match finished, not just one of them."""
    replica_a = events.subscribe()
    replica_b = events.subscribe()
    try:
        events.publish({"type": "match_finished", "match_id": "m_2"})
        assert events.listen(replica_a, timeout=2.0)["match_id"] == "m_2"
        assert events.listen(replica_b, timeout=2.0)["match_id"] == "m_2"
    finally:
        replica_a.close()
        replica_b.close()


def test_publish_reports_how_many_subscribers_received_it(events):
    pubsub = events.subscribe()
    try:
        assert events.publish({"type": "ping"}) >= 1
    finally:
        pubsub.close()


def test_publishing_with_no_subscribers_is_not_an_error(events):
    """A match can finish while no client is watching."""
    assert events.publish({"type": "match_finished", "match_id": "m_3"}) == 0


def test_events_round_trip_nested_payloads(events):
    pubsub = events.subscribe()
    try:
        payload = {"type": "match_finished", "match_id": "m_4",
                   "standings": [{"car_id": "VEL-01", "position": 1}]}
        events.publish(payload)
        assert events.listen(pubsub, timeout=2.0) == payload
    finally:
        pubsub.close()


def test_the_channel_name_is_stable(events):
    """Workers and replicas are deployed separately; a rename splits them."""
    assert CHANNEL == "piwall:events:match"
