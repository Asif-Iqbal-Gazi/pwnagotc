"""
Tri-mode runtime strategy — see docs/MODES.md.

Three modes decide *who drives the attack + channel hopping*:

  Manual  😴  daemon hops+captures, no attack; agent just consumes + uploads
  Agent   🐍  daemon hands channel control over (auto_stop); the PYTHON agent
              drives recon → hop → assoc/deauth (the original pwnagotchi path)
  Engine  ⚡  daemon self-drives hop+capture+attack (--auto); agent consumes

They are runtime-switchable (no reboot): each mode's enter() reconfigures the
wificapc daemon, and the main loop just calls the current mode's tick().
"""
import logging
import time


class Mode:
    name = "base"
    badge = "?"   # emoji, for logs + the web UI
    tag = "?"     # 3-char ASCII, for the e-ink/LCD display (no emoji there)

    def __init__(self, agent):
        self.agent = agent

    def enter(self):
        pass

    def leave(self):
        pass

    def tick(self):
        time.sleep(5)


class ManualMode(Mode):
    name = "manual"
    badge = "\U0001F634"  # 😴
    tag = "MAN"

    def enter(self):
        # Daemon keeps hopping + capturing so passive handshakes still land;
        # just hold the attack.
        self.agent._daemon_auto_start()
        self.agent.set_attack(False)

    def tick(self):
        self.agent.next_epoch()
        time.sleep(5)


class EngineMode(Mode):
    name = "engine"
    badge = "⚡"  # ⚡
    tag = "ENG"

    def enter(self):
        self.agent._daemon_auto_start()
        self.agent.set_attack(True)

    def tick(self):
        # Consumer: the daemon drives; epochs advance from the attack.*/
        # handshake.done events our handlers track.
        self.agent.next_epoch()
        time.sleep(5)


class AgentMode(Mode):
    name = "agent"
    badge = "\U0001F40D"  # 🐍
    tag = "AGT"

    def enter(self):
        # Take channel control away from the daemon so we don't fight it.
        self.agent._daemon_auto_stop()

    def leave(self):
        # Stop our hopping so the next mode's auto_start owns the radio again.
        try:
            self.agent._wificapc.cmd("hop_stop")
        except Exception:
            pass

    def tick(self):
        a = self.agent
        a.recon()
        for ch, aps in a.get_access_points_by_channel():
            time.sleep(1)
            a.set_channel(ch)
            if not a.is_stale() and a.any_activity():
                logging.info("%d access points on channel %d", len(aps), ch)
            for ap in aps:
                a.associate(ap)
                for sta in ap["clients"]:
                    a.deauth(ap, sta)
                    time.sleep(1)  # delay to not trigger nexmon firmware bugs
        a.next_epoch()


MODES = {
    ManualMode.name: ManualMode,
    AgentMode.name:  AgentMode,
    EngineMode.name: EngineMode,
}


def badge_for(name):
    cls = MODES.get(name)
    return cls.badge if cls else "?"


def tag_for(name):
    cls = MODES.get(name)
    return cls.tag if cls else "?"
