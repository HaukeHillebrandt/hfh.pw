(function () {
  var filters = document.querySelectorAll('.filter');
  var search = document.getElementById('search');
  var rows = document.querySelectorAll('.index .row');
  var years = document.querySelectorAll('.index .year');
  var empty = document.querySelector('.empty');
  var filter = 'all';
  var fullText = null;   // slug -> lowercase doc text, fetched on first search

  function loadIndex() {
    if (fullText !== null) return;
    fullText = {};
    fetch('search.json')
      .then(function (r) { return r.json(); })
      .then(function (d) { fullText = d; apply(); })
      .catch(function () {});
  }

  function apply() {
    var q = (search && search.value || '').toLowerCase().trim();
    var shown = 0;
    rows.forEach(function (r) {
      var ok = (filter === 'all' || r.dataset.source === filter) &&
        (!q || r.dataset.title.indexOf(q) !== -1 ||
         (r.dataset.slug && fullText && (fullText[r.dataset.slug] || '').indexOf(q) !== -1));
      r.hidden = !ok;
      if (ok) shown++;
    });
    years.forEach(function (y) {
      var el = y.nextElementSibling, any = false;
      while (el && !el.classList.contains('year')) {
        if (!el.hidden) { any = true; break; }
        el = el.nextElementSibling;
      }
      y.hidden = !any;
    });
    if (empty) empty.hidden = shown > 0;
  }

  filters.forEach(function (b) {
    b.addEventListener('click', function () {
      filters.forEach(function (x) {
        x.classList.remove('active');
        x.setAttribute('aria-pressed', 'false');
      });
      b.classList.add('active');
      b.setAttribute('aria-pressed', 'true');
      filter = b.dataset.filter;
      apply();
    });
  });

  if (search) {
    search.addEventListener('input', function () { loadIndex(); apply(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && document.activeElement !== search &&
          !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
        e.preventDefault();
        search.focus();
      }
    });
  }
})();
