document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".progress-fill").forEach(function (bar) {
        const progress = Number(bar.dataset.progress);
        bar.style.width = Math.max(0, Math.min(100, progress)) + "%";
    });
});