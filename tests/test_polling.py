import os
import tempfile
import unittest
from pathlib import Path

from housemon import polling


class Lapsed(Exception):
    pass


class Failed(Exception):
    pass


class Clock:
    """A stop event that waits for nothing: it notes each wait, calls then(n) after the nth, and sets itself after
    `stop_after` of them."""

    def __init__(self, stop_after: int, then=lambda n: None):
        self.waits: list[float] = []
        self.stop_after = stop_after
        self.then = then

    def is_set(self) -> bool:
        return len(self.waits) >= self.stop_after

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.then(len(self.waits))
        return self.is_set()


class PollGentlyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.sign_in = Path(self.dir.name) / "sign-in.json"
        self.sign_in.write_text("{}")
        self.logged, self.published, self.failures = [], [], []

    def tearDown(self):
        self.dir.cleanup()

    def run_polls(self, outcomes, clock, interval=60.0):
        """Poll until the clock stops, each poll the next of outcomes: an exception to raise, else the items it gives,
        the last one again once they run out."""
        outcomes = list(outcomes)
        polls = []

        def poll(now):
            polls.append(now)
            outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        polling.poll_gently(
            poll, self.published.append, clock, interval, sign_in=self.sign_in, lapsed=Lapsed, failed=(Failed,),
            sign_in_again="sign-in --command", log=self.logged.append, describe=lambda e: f"it failed: {e}",
            on_failure=self.failures.append,
        )
        return polls

    def test_it_polls_every_interval_while_polls_work(self):
        clock = Clock(3)
        self.run_polls([["a"]], clock)
        self.assertEqual(clock.waits, [60.0, 60.0, 60.0])
        self.assertEqual(self.published, ["a", "a", "a"])
        self.assertEqual(self.logged, [])

    def test_it_backs_off_while_polls_fail_and_comes_back(self):
        clock = Clock(6)
        self.run_polls([Failed("502"), Failed("502"), Failed("502"), Failed("502"), ["a"]], clock)
        self.assertEqual(clock.waits, [120.0, 300.0, 600.0, 600.0, 60.0, 60.0])
        self.assertEqual(self.logged, [
            "it failed: 502; trying again in 2 min", "it failed: 502; trying again in 5 min",
            "it failed: 502; trying again in 10 min", "it failed: 502; trying again in 10 min", "polling again as usual",
        ])
        self.assertEqual(len(self.failures), 4)

    def test_backing_off_never_waits_less_than_the_interval(self):
        clock = Clock(3)
        self.run_polls([Failed("x"), Failed("x"), ["a"]], clock, interval=180.0)
        self.assertEqual(clock.waits, [180.0, 300.0, 180.0])

    def test_a_lapsed_sign_in_is_left_alone_until_its_signed_in_again(self):
        def then(n):
            if n == 3:  # signing in again replaces the file
                part = self.sign_in.with_name("new.json")
                part.write_text('{"token": "new"}')
                os.replace(part, self.sign_in)

        clock = Clock(5, then)
        polls = self.run_polls([Lapsed("ecobee no longer takes the saved sign-in"), ["a"]], clock)
        self.assertEqual(len(polls), 3)  # the lapsed one, then one after each of the two intervals: none while it waited
        self.assertEqual(clock.waits, [polling.SIGN_IN_CHECK] * 3 + [60.0, 60.0])
        self.assertEqual(self.logged, [
            "ecobee no longer takes the saved sign-in; sign in again with sign-in --command, and until then it's left alone",
            "signed in again; polling again",
        ])
        self.assertEqual([str(e) for e in self.failures], ["ecobee no longer takes the saved sign-in"])

    def test_a_missing_sign_in_waits_for_one(self):
        self.sign_in.unlink()
        clock = Clock(4, lambda n: n == 2 and self.sign_in.write_text("{}"))
        polls = self.run_polls([Lapsed("no sign-in"), ["a"]], clock)
        self.assertEqual(len(polls), 3)
        self.assertEqual(clock.waits, [polling.SIGN_IN_CHECK] * 2 + [60.0, 60.0])

    def test_stopping_while_it_waits_for_a_sign_in_ends_it(self):
        clock = Clock(2)
        polls = self.run_polls([Lapsed("lapsed")], clock)
        self.assertEqual(len(polls), 1)
        self.assertEqual(clock.waits, [polling.SIGN_IN_CHECK] * 2)


if __name__ == "__main__":
    unittest.main()
