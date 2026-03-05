/* brain-mcp dashboard — Deep Space Neural JS */

// ═══════════════════════════════════════════════════════════════
// COUNTER ANIMATION
// ═══════════════════════════════════════════════════════════════

function animateCounter(el, target, duration) {
    if (duration === undefined) duration = 2000;
    var start = 0;
    var step = function(ts) {
        if (!start) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.floor(eased * target).toLocaleString();
        if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
}

/**
 * Find all elements with data-counter attribute and animate them.
 * Called after htmx swaps in stats content.
 */
function initCounters(root) {
    if (!root) root = document;
    var counters = root.querySelectorAll('[data-counter]');
    counters.forEach(function(el) {
        var target = parseInt(el.getAttribute('data-counter'), 10);
        if (!isNaN(target) && target > 0) {
            el.textContent = '0';
            animateCounter(el, target);
        }
    });
}

// ═══════════════════════════════════════════════════════════════
// HTMX EVENT HOOKS — trigger counter animation after swap
// ═══════════════════════════════════════════════════════════════

document.addEventListener('htmx:afterSwap', function(evt) {
    initCounters(evt.detail.target);
});

// Also run on initial page load (for any pre-rendered counters)
document.addEventListener('DOMContentLoaded', function() {
    initCounters();
});

// ═══════════════════════════════════════════════════════════════
// SEARCH DEBOUNCE (500ms)
// ═══════════════════════════════════════════════════════════════

var searchTimer = null;
function debounceSearch(el, url) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() {
        htmx.ajax('GET', url + '?q=' + encodeURIComponent(el.value), '#search-results');
    }, 500);
}

// ═══════════════════════════════════════════════════════════════
// SSE HELPER — for long-running tasks
// ═══════════════════════════════════════════════════════════════

function connectSSE(taskId, targetId) {
    var source = new EventSource('/api/tasks/' + taskId + '/stream');
    var target = document.getElementById(targetId);

    source.onmessage = function(event) {
        var data = JSON.parse(event.data);
        if (target) {
            target.innerHTML = data.message || data.progress || '';
        }
        if (data.status === 'done' || data.status === 'failed') {
            source.close();
            // Refresh stats after task completes
            htmx.ajax('GET', '/api/stats/overview', '#stats-cards');
        }
    };

    source.onerror = function() {
        source.close();
    };
}
