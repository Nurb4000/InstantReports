// Shared HTML-escaping helper for values interpolated into innerHTML.
// Defined early (loaded in <head>) so admin/designer inline scripts can use it
// without depending on execution order. Prevents DOM XSS from server-supplied
// names, cron strings, commit messages, audit details, and AI responses.
window.escapeHtml = function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};
