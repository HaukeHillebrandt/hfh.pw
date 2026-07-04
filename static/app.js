(function () {
  var tabs = document.querySelectorAll('.tab');
  var search = document.getElementById('search');
  var posts = document.querySelectorAll('.post');
  var filter = 'all';

  function apply() {
    var q = (search.value || '').toLowerCase().trim();
    posts.forEach(function (p) {
      var okSource = filter === 'all' || p.dataset.source === filter;
      var okText = !q || p.dataset.title.indexOf(q) !== -1;
      p.classList.toggle('hidden', !(okSource && okText));
    });
  }

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
  if (search) search.addEventListener('input', apply);
})();
