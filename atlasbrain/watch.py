"""Coalesced polling in the service's existing index thread; no extra daemon."""
import time
from pathlib import Path


class IndexQueue:
    def __init__(self, scan, index, clock=time.monotonic, settle=2, maximum_wait=10):
        self.scan, self.index, self.clock = scan, index, clock
        self.settle, self.maximum_wait = settle, maximum_wait
        self.states = {}

    def step(self, vault):
        vault = Path(vault)
        signature = {}
        for name, path in self.scan(vault).items():
            try:
                st = path.stat()
                signature[name] = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                continue
        now = self.clock()
        state = self.states.setdefault(str(vault), {'indexed': None, 'seen': None, 'since': now, 'changed': now, 'checked': now})
        if signature != state['seen']:
            if state['seen'] == state['indexed']:
                state['since'] = now
            state.update(seen=signature, changed=now)
        pending = signature != state['indexed']
        stable = now - state['changed'] >= self.settle
        overdue = now - state['since'] >= self.maximum_wait
        # Periodic reconciliation also backfills embeddings and checks schema updates.
        reconcile = now - state['checked'] >= 300
        if (pending and (stable or overdue)) or reconcile:
            result = self.index(vault)
            if not result.get('skipped') and not result.get('erro'):
                state.update(indexed=signature, checked=now)
            return result
        return None

    def retain(self, paths):
        allowed = {str(Path(p)) for p in paths}
        self.states = {p: s for p, s in self.states.items() if p in allowed}
