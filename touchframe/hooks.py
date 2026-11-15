"""Webhooks: other systems put alerts on the screen by posting to TouchFrame.

POST /api/hook/alert    {"title", "text", "priority", "seconds", "key"}, or {"key", "end": true}
POST /api/hook/grafana  Grafana's webhook contact point
"""
import time


class HookError(Exception):
    pass


class Hooks:
    def __init__(self, cfg, alerts):
        self.cfg = cfg["webhooks"]
        self.alerts = alerts
        self._fired = {}                # Grafana alerts that are up or were shown: key -> when

    def _until(self, seconds=None):
        seconds = self.cfg["hold_seconds"] if seconds is None else seconds
        return int(time.time() * 1000) + int(max(1, min(float(seconds), 86400)) * 1000)

    def alert(self, body):
        if not isinstance(body, dict):
            raise HookError("send a JSON object")
        key = f"hook:{body['key']}" if body.get("key") else f"hook:{time.time_ns()}"
        if body.get("end"):
            self.alerts.end(key, int(time.time() * 1000), stopped=True)
            return {"ok": True, "ended": key[5:]}
        text = body.get("text") or body.get("message") or ""
        text = [str(t) for t in text] if isinstance(text, list) else str(text).splitlines()
        if not any(t.strip() for t in text):
            raise HookError('"text" is missing')
        priority = body.get("priority", "normal")
        if priority not in ("normal", "high", "emergency"):
            raise HookError('"priority" is normal, high or emergency')
        self.alerts.show(key, str(body.get("source") or "webhook"), str(body.get("title") or "Alert"),
                         text, priority=priority, until_ms=self._until(body.get("seconds")))
        return {"ok": True, "key": key[5:]}

    def grafana(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
            raise HookError("this isn't a Grafana webhook")
        shown = 0
        for a in body["alerts"]:
            labels, notes = a.get("labels") or {}, a.get("annotations") or {}
            key = f"grafana:{a.get('fingerprint') or labels.get('alertname')}:{a.get('startsAt', '')}"
            if a.get("status") == "resolved":
                self._fired.pop(key, None)
                self.alerts.end(key, int(time.time() * 1000), stopped=True)
                continue
            if key in self._fired:
                continue                # Grafana repeats firing alerts; once is enough
            if len(self._fired) > 1000:
                self._fired.clear()
            self._fired[key] = time.time()
            severity = str(labels.get("severity", "")).lower()
            priority = ("emergency" if severity in self.cfg["emergency_severities"]
                        else "high" if severity in self.cfg["high_severities"] else "normal")
            name = labels.get("alertname") or body.get("title") or "Grafana alert"
            text = notes.get("summary") or notes.get("description") or name
            self.alerts.show(key, "Grafana", f"Grafana · {name}", str(text).splitlines(),
                             priority=priority, until_ms=self._until())
            shown += 1
        return {"ok": True, "shown": shown}
