// Apply theme immediately before first paint to prevent flash.
// This runs as a blocking script before the app bundle loads.
(function() {
  var saved = localStorage.getItem('preferred-theme') || 'auto';
  var theme = saved === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : saved;

  var dark = theme === 'dark';
  var bg = dark ? '#121212' : '#f8f8f8';
  var fg = dark ? '#fff' : '#333';

  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme;

  var s = document.createElement('style');
  s.id = 'theme-init';
  s.textContent = 'html,body,#root{background:' + bg + ';color:' + fg + '}';
  document.head.appendChild(s);

  document.documentElement.classList.add('preload');
})();
