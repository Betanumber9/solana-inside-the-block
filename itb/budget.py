"""Spend cap. Two independent checks, either one stops live sending:
1) ledger: confirmed fees + worst-case estimate of everything still unresolved + the next burst <= cap;
2) balances: (sum of our wallets at start) - (sum now) <= cap. Rent parked in sub-wallets is not spend and is
   counted in the balance sum, so funding sub-wallets moves money without consuming the cap (only its fee does)."""
import json
import pathlib
import time


class CapExceeded(Exception):
    pass


class Budget:
    def __init__(self, path, cap_lamports):
        self.path = pathlib.Path(path)
        self.cap = cap_lamports
        self.state = {"cap": cap_lamports, "confirmed": 0, "pending": {}, "start_balance": None, "history": []}
        if self.path.exists():
            self.state.update(json.loads(self.path.read_text()))
            self.state["cap"] = cap_lamports

    def _save(self):
        self.path.write_text(json.dumps(self.state, indent=1))

    @property
    def committed(self):
        return self.state["confirmed"] + sum(self.state["pending"].values())

    def reserve(self, key, lamports):
        if self.committed + lamports > self.cap:
            raise CapExceeded("ledger: %d committed + %d > cap %d" % (self.committed, lamports, self.cap))
        self.state["pending"][key] = lamports
        self._save()

    def settle(self, key, actual):
        """actual: lamports really charged for the reserved item (0 if nothing landed)."""
        self.state["pending"].pop(key, None)
        self.state["confirmed"] += actual
        self.state["history"].append({"t": time.time(), "key": key, "fee": actual})
        self._save()

    def check_balance(self, total_now):
        if self.state["start_balance"] is None:
            self.state["start_balance"] = total_now
            self._save()
        spent = self.state["start_balance"] - total_now
        if spent > self.cap:
            raise CapExceeded("balance: spent %d > cap %d" % (spent, self.cap))
        return spent
