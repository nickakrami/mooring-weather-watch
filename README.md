The current logic sends an email when:

Wind first reaches 80% of the applicable limit → Advisory.
Wind reaches 100% → Warning, even if an Advisory was already sent.
Maximum forecast utilisation increases by at least 5 percentage points compared with the last notification.
All monitored horizons fall below 80% again → All Clear.

Example:

82% → Advisory sent
84% → no email
87% → Warning update sent because it has increased 5 percentage points from the last notification
89% → no email
101% → Warning sent because it crossed 100%
104% → no email
107% → Warning update sent because it increased at least another 5 percentage points
Below 80% at all horizons → All Clear sent

Therefore, during a sustained but stable exceedance, you might receive one Advisory, one Warning if it escalates, occasional material-increase updates, and finally one All Clear—not hourly repetition.

One exception: if GitHub loses the small cached alert-state file, the system may treat the condition as new and send another alert. With the hourly workflow regularly accessing the cache, that should be uncommon.
