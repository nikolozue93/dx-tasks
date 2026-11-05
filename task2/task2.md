# Incident Investigation Plan — Task 2

**Nikoloz Beridze | Application Support Engineer Assessment**

## 1. Investigation Plan

At 14:02, the API error rate increases from 0.2% to 7%, two of five instances reach 95% CPU usage, and an enterprise customer reports intermittent 502 errors. Traffic volume remains normal, and there have been no deployments in the last 24 hours.

My first priority would be to understand the impact and how widespread the issue is, before trying to identify the root cause.

### Step 1 — Determine scope and impact

I would start with the metrics dashboard and application logs to check whether the errors affect only `/api/orders` or other endpoints as well. I'd also compare error rates, response times, and request counts across the five instances to see whether the failures are concentrated on the two instances with high CPU usage.

I would check how many customers are affected, whether successful requests are still being processed, and whether the situation is getting worse. This would help determine urgency and whether immediate escalation is needed.

### Step 2 — Investigate the failures

The logs show that requests to `/api/orders` are failing with 502 errors after approximately five seconds, accompanied by upstream timeout messages.

I'd investigate communication between the API and internal service, checking response times, timeout frequency, and relevant errors. Since the architecture is API → internal service → database, a delay in one component could affect the entire request.

The successful `/api/health` response is also worth noting. It confirms that the health endpoint responds, but it doesn't necessarily mean important operations or downstream dependencies are functioning correctly.

### Step 3 — Check resources and connection availability

Two instances have unusually high CPU usage. I'd compare their request volumes and types of work with the other three instances to understand whether they're receiving more traffic or performing more expensive operations.

The connection pool reports 100 active connections and none idle at 14:02:10. That could leave new requests waiting for a free connection, but one log entry doesn't show whether it stayed full. I'd first establish which component owns the pool, then check connection usage over time, acquisition wait times, and how long connections remain checked out.

### Step 4 — Form and test hypotheses

1. **Slow downstream operations.** The 4.8-second slow-query warning and roughly five-second upstream timeouts could be connected. I'd compare database query times with internal-service response times, check for blocked queries or locks, and see whether the same requests can be followed through the logs.

2. **Connection pool pressure.** If operations hold connections too long, new requests may wait or time out. I'd check whether requests are actually waiting to acquire connections, how long connections are held, and whether they're returned correctly.

3. **Retries increasing load.** The API logs show retries to the internal service. I'd check how frequently they happen, whether they add significant work, and what retry limits and delays are configured.

4. **Uneven work across instances.** I'd compare request counts, endpoint types, and CPU usage across the five instances to see why only two are under pressure.

These are hypotheses, not confirmed causes. The logs don't establish which component owns the connection pool or prove that the slow query belongs to the same request that returned 502.

Traffic is normal overall, and there have been no deployments in 24 hours. I'd still check for configuration changes, scheduled jobs, database maintenance, or infrastructure events around 14:02, since these can affect the service without a new release.

### Step 5 — Consider safe mitigation

If errors are concentrated on the two high-CPU instances, I'd consider taking them out of traffic, but only after checking whether the other three can handle the load. If retries are adding pressure, reducing them may help. I wouldn't restart instances or increase the pool size blindly, since that might hide the issue or put more pressure on a shared dependency. Any change should follow production procedures, and I'd watch error rate and latency afterward.

## 2. Escalation Decision

I would escalate the incident. At the 30-minute mark, the error rate is still 5–8%, two instances remain at high CPU usage, and the problem is unresolved.

I wouldn't wait the full 30 minutes if the initial checks show wider customer impact or a problem beyond my access. I'd notify the on-call lead early if needed, and escalate promptly if the impact is serious. At 30 minutes, with continued errors and no recovery, escalation is clearly necessary.

I'd follow the escalation procedure, starting with the designated L2/on-call engineer or responsible service owner. Depending on the findings, internal-service developers, database engineers, or infrastructure engineers may need to be involved.

My escalation would include:

- Incident start time, current status, and reported customer impact.
- Affected endpoints and instances, where confirmed; changes in error rate and CPU usage.
- Log excerpts showing 502s, upstream timeouts, connection-pool saturation, and slow-query warning.
- Checks already performed, current hypotheses, uncertainties, and help needed.

I would continue monitoring and keep the customer updated with verified information. Root cause doesn't need to be confirmed before involving the appropriate engineers.

## 3. Preventive Improvements

### 1 — Monitor connection pools and request performance

I'd monitor active connections, requests waiting for a connection, acquisition time, and connection hold time. Alerts should consider sustained high utilization together with growing wait times rather than a single high reading.

This mainly improves early detection. Thresholds would need tuning to avoid false alarms during normal busy periods.

### 2 — Review retry behavior and request timeouts

Retries may add load to an already slow internal service. I'd review retry limits, add backoff with jitter where appropriate, and enforce sensible total request deadlines. Retrying must also be safe for the operation involved.

This primarily reduces impact, but stricter limits could cause some temporary errors to be returned to clients instead of automatically recovering.

### 3 — Improve health checks and troubleshooting information

The health endpoint returned 200 while business requests failed. I'd monitor error rates and latency for important endpoints separately, with lightweight, safe end-to-end checks where appropriate. Consistent request IDs across the API and internal service would help connect failed requests to downstream operations.

This improves detection and investigation. Extra checks and logs must be lightweight and avoid exposing sensitive customer data.

## Conclusion

The logs suggest upstream delays, connection pressure, and possibly slow database operations. They may be related, but there's not enough evidence to confirm which one started the incident. I'd first establish impact, use logs and metrics to test the likely causes, and involve the responsible engineers while the issue is still affecting customers.