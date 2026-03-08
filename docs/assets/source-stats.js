(() => {
  const widgets = [...document.querySelectorAll('[data-gh-repo]')];
  if (!widgets.length) return;

  const repos = [...new Set(widgets.map((widget) => widget.dataset.ghRepo).filter(Boolean))];

  const formatCount = (value) =>
    new Intl.NumberFormat(undefined, {
      notation: value >= 1000 ? 'compact' : 'standard',
      maximumFractionDigits: 1,
    }).format(value);

  const updateRepoStats = (repo, stats) => {
    for (const widget of widgets.filter((node) => node.dataset.ghRepo === repo)) {
      const stars = widget.querySelector('[data-gh-stat="stars"]');
      const forks = widget.querySelector('[data-gh-stat="forks"]');
      if (stars) stars.textContent = `${formatCount(stats.stargazers_count)} stars`;
      if (forks) forks.textContent = `${formatCount(stats.forks_count)} forks`;
    }
  };

  const markUnavailable = (repo) => {
    for (const widget of widgets.filter((node) => node.dataset.ghRepo === repo)) {
      const stars = widget.querySelector('[data-gh-stat="stars"]');
      const forks = widget.querySelector('[data-gh-stat="forks"]');
      if (stars) stars.textContent = 'stars';
      if (forks) forks.textContent = 'forks';
    }
  };

  for (const repo of repos) {
    fetch(`https://api.github.com/repos/${repo}`)
      .then((response) => {
        if (!response.ok) throw new Error(`GitHub API request failed: ${response.status}`);
        return response.json();
      })
      .then((data) => updateRepoStats(repo, data))
      .catch(() => markUnavailable(repo));
  }
})();
