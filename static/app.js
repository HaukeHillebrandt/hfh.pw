(function () {
  var tabs = document.querySelectorAll('.tab');
  var search = document.getElementById('search');
  var posts = document.querySelectorAll('.post');
  var filter = 'all';
  var fullText = null;   // slug -> lowercase doc text, lazy-loaded on first search

  function loadIndex() {
    if (fullText !== null) return;
    fullText = {};       // sentinel so we fetch once
    fetch('search.json')
      .then(function (r) { return r.json(); })
      .then(function (d) { fullText = d; apply(); })
      .catch(function () {});
  }

  function apply() {
    var q = (search.value || '').toLowerCase().trim();
    posts.forEach(function (p) {
      var okSource = filter === 'all' || p.dataset.source === filter;
      var okText = !q || p.dataset.title.indexOf(q) !== -1 ||
        (p.dataset.slug && fullText && (fullText[p.dataset.slug] || '').indexOf(q) !== -1);
      p.classList.toggle('hidden', !(okSource && okText));
    });
    document.querySelectorAll('.year-sep').forEach(function (sep) {
      var el = sep.nextElementSibling, any = false;
      while (el && !el.classList.contains('year-sep')) {
        if (el.classList.contains('post') && !el.classList.contains('hidden')) {
          any = true;
          break;
        }
        el = el.nextElementSibling;
      }
      sep.classList.toggle('hidden', !any);
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && search && document.activeElement !== search &&
        !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault();
      search.focus();
    }
  });

  tabs.forEach(function (t) {
    t.setAttribute('aria-pressed', t.classList.contains('active') ? 'true' : 'false');
    t.addEventListener('click', function () {
      tabs.forEach(function (x) {
        x.classList.remove('active');
        x.setAttribute('aria-pressed', 'false');
      });
      t.classList.add('active');
      t.setAttribute('aria-pressed', 'true');
      filter = t.dataset.filter;
      apply();
    });
  });
  if (search) {
    search.addEventListener('input', function () {
      loadIndex();
      apply();
    });
  }
})();
