<script>
// Minimal JSON→HTML renderer for DomainBookmarks category pages
document.addEventListener('DOMContentLoaded', async () => {
  const root = document.getElementById('category-root');
  if (!root) return;
  const url = root.dataset.json;
  try {
    const resp = await fetch(url, { cache: 'no-store' });
    const data = await resp.json();

    // sort groups and items alphabetically (case-insensitive)
    const groups = (data.groups || []).slice().sort((a,b)=>a.name.localeCompare(b.name, undefined,{sensitivity:'base'}));
    groups.forEach(g => g.items.sort((a,b)=> (a.title||'').localeCompare(b.title||'', undefined,{sensitivity:'base'})));

    // build HTML
    const frag = document.createDocumentFragment();
    if (!groups.length) {
      const p = document.createElement('p'); p.textContent = 'No items yet.';
      frag.appendChild(p);
    } else {
      groups.forEach(g => {
        const h = document.createElement('h3'); h.textContent = g.name; frag.appendChild(h);
        const ul = document.createElement('ul'); ul.className = 'cards-grid';
        g.items.forEach(it => {
          const li = document.createElement('li'); li.className = 'card';
          li.innerHTML = `
            <a href="${it.url}" target="_blank" rel="nofollow noopener">
              <strong>${it.title || it.url}</strong><br>
              <em>${(new URL(it.url)).hostname}</em>
              <p>${it.description || ''}</p>
            </a>`;
          ul.appendChild(li);
        });
        frag.appendChild(ul);
      });
    }
    root.innerHTML = '';
    root.appendChild(frag);
  } catch (e) {
    root.innerHTML = '<p>Failed to load items.</p>';
    console.error(e);
  }
});
</script>
