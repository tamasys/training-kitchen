/**
 * autostop-guard.js
 * Shared auto-stop warning overlay for all Training Kitchen UIs.
 *
 * Include this script in any page that should warn the user before
 * the pod is automatically stopped due to inactivity.
 *
 * All coordinator API calls use the /api/coordinator/ relative path, which
 * every nginx server block (port 80, 5002, 8676) proxies to the coordinator.
 * No URL construction or platform detection is needed here.
 */
(function () {
    'use strict';

    // ── Config ───────────────────────────────────────────────────────────────
    const WARN_SECONDS = 120;   // Show overlay when this many seconds remain
    const POLL_IDLE_MS = 30000; // Normal poll interval
    const POLL_WARN_MS = 5000;  // Fast poll interval when overlay is visible

    // All coordinator calls go via the stable /api/coordinator/ prefix, proxied
    // to the coordinator by whichever nginx server block is serving this page.
    const COORD = '/api/coordinator';

    // ── Inject styles ────────────────────────────────────────────────────────
    const style = document.createElement('style');
    style.textContent = `
        #as-overlay {
            display: none;
            position: fixed;
            inset: 0;
            z-index: 99999;
            background: rgba(10, 8, 8, 0.82);
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            align-items: center;
            justify-content: center;
            font-family: 'Inter', 'Segoe UI', sans-serif;
        }
        #as-overlay.as-visible { display: flex; }

        #as-box {
            background: #1e1a19;
            border: 1px solid #7c3b3b;
            border-radius: 16px;
            padding: 2rem 2.25rem;
            max-width: 420px;
            width: 90vw;
            box-shadow: 0 0 60px rgba(200,60,60,0.25), 0 8px 32px rgba(0,0,0,0.6);
            text-align: center;
            animation: as-pop 0.25s cubic-bezier(0.34,1.56,0.64,1);
        }
        @keyframes as-pop {
            from { transform: scale(0.88); opacity: 0; }
            to   { transform: scale(1);   opacity: 1; }
        }

        #as-icon  { font-size: 2.4rem; margin-bottom: 0.5rem; }
        #as-title {
            font-size: 1.15rem;
            font-weight: 700;
            color: #f87171;
            margin-bottom: 0.4rem;
        }
        #as-sub {
            font-size: 0.82rem;
            color: #8a8075;
            margin-bottom: 1.25rem;
            line-height: 1.5;
        }

        #as-timer {
            font-size: 2.8rem;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
            letter-spacing: -0.02em;
            color: #f87171;
            margin-bottom: 1.4rem;
            transition: color 0.3s;
        }
        #as-timer.as-critical { color: #ef4444; animation: as-pulse 0.8s infinite; }
        @keyframes as-pulse { 0%,100%{opacity:1} 50%{opacity:0.55} }

        #as-btn-here {
            width: 100%;
            padding: 0.85rem;
            border-radius: 10px;
            border: none;
            background: #22c55e;
            color: #000;
            font-family: inherit;
            font-size: 0.95rem;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            margin-bottom: 0.6rem;
        }
        #as-btn-here:hover { background: #16a34a; transform: translateY(-1px); }
        #as-btn-here:active { transform: translateY(0); }

        #as-btn-disable {
            background: transparent;
            border: 1px solid #3a3330;
            color: #8a8075;
            padding: 0.55rem;
            border-radius: 8px;
            width: 100%;
            font-family: inherit;
            font-size: 0.8rem;
            cursor: pointer;
            transition: border-color 0.2s, color 0.2s;
        }
        #as-btn-disable:hover { border-color: #8a8075; color: #e2d9d0; }

        #as-stopping {
            display: none;
        }
        #as-stopping.as-visible {
            display: block;
        }
        #as-stopping p {
            font-size: 0.9rem;
            color: #f87171;
            font-weight: 600;
            margin-top: 0.5rem;
        }
    `;
    document.head.appendChild(style);

    // ── Inject HTML ──────────────────────────────────────────────────────────
    const overlay = document.createElement('div');
    overlay.id = 'as-overlay';
    overlay.innerHTML = `
        <div id="as-box">
            <div id="as-icon">⏳</div>
            <div id="as-title">Pod Shutting Down Soon</div>
            <div id="as-sub">
                Auto-stop is enabled. The pod will stop due to inactivity in:
            </div>
            <div id="as-timer">–:––</div>
            <div id="as-normal-actions">
                <button id="as-btn-here" onclick="window.__asStillHere()">
                    ✋ I'm Still Here — Reset Timer
                </button>
                <button id="as-btn-disable" onclick="window.__asDisable()">
                    Disable auto-stop
                </button>
            </div>
            <div id="as-stopping">
                <div id="as-icon">⚡</div>
                <p>Stop command sent — pod is shutting down…</p>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // ── State ────────────────────────────────────────────────────────────────
    let _visible = false;
    let _pollTimer = null;
    let _lastSeconds = null;

    // ── API calls ────────────────────────────────────────────────────────────
    async function fetchStatus() {
        const r = await fetch(COORD + '/autostop');
        return r.json();
    }

    window.__asStillHere = async function () {
        try {
            await fetch(COORD + '/autostop/ping', { method: 'POST' });
            hideOverlay();
            schedulePoll(POLL_IDLE_MS);
        } catch (e) { console.warn('[autostop-guard] ping failed', e); }
    };

    window.__asDisable = async function () {
        try {
            await fetch(COORD + '/autostop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: false }),
            });
            hideOverlay();
            schedulePoll(POLL_IDLE_MS);
        } catch (e) { console.warn('[autostop-guard] disable failed', e); }
    };

    // ── Overlay helpers ──────────────────────────────────────────────────────
    function showOverlay(secondsRemaining, stopping) {
        overlay.classList.add('as-visible');
        _visible = true;

        const timerEl = document.getElementById('as-timer');
        const normalEl = document.getElementById('as-normal-actions');
        const stoppingEl = document.getElementById('as-stopping');

        if (stopping) {
            timerEl.textContent = '';
            normalEl.style.display = 'none';
            stoppingEl.classList.add('as-visible');
            document.getElementById('as-icon').textContent = '⚡';
            document.getElementById('as-title').textContent = 'Pod Shutting Down';
        } else {
            const m = Math.floor(secondsRemaining / 60);
            const s = Math.floor(secondsRemaining % 60);
            timerEl.textContent = `${m}:${String(s).padStart(2, '0')}`;
            timerEl.className = 'as-timer' + (secondsRemaining < 30 ? ' as-critical' : '');
            normalEl.style.display = '';
            stoppingEl.classList.remove('as-visible');
        }
    }

    function hideOverlay() {
        overlay.classList.remove('as-visible');
        _visible = false;

        // Reset stopping state for next time
        document.getElementById('as-icon').textContent = '⏳';
        document.getElementById('as-title').textContent = 'Pod Shutting Down Soon';
        document.getElementById('as-normal-actions').style.display = '';
        document.getElementById('as-stopping').classList.remove('as-visible');
    }

    // ── Poll loop ────────────────────────────────────────────────────────────
    async function poll() {
        try {
            const data = await fetchStatus();

            // Only relevant on RunPod
            if (!data.enabled && !_visible) {
                schedulePoll(POLL_IDLE_MS);
                return;
            }

            const secs = data.seconds_remaining;
            const warn = data.enabled && data.timer_running &&
                secs != null && secs <= WARN_SECONDS;
            const stopping = data.stopping;

            if (stopping) {
                showOverlay(0, true);
                // No need to keep polling once stopping
                return;
            }

            if (warn) {
                showOverlay(secs, false);
                schedulePoll(POLL_WARN_MS);
            } else {
                if (_visible) hideOverlay();
                schedulePoll(POLL_IDLE_MS);
            }
        } catch (e) {
            // Coordinator not reachable — hide overlay if visible
            if (_visible) hideOverlay();
            schedulePoll(POLL_IDLE_MS);
        }
    }

    function schedulePoll(ms) {
        if (_pollTimer) clearTimeout(_pollTimer);
        _pollTimer = setTimeout(poll, ms);
    }

    // Kick off — small delay so the page's own JS initialises first
    setTimeout(poll, 2000);
})();
