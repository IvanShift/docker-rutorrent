import {
  PanelLabelSelection,
  CategoryListStatistic,
  TextSearch,
  TorrentLabelTree,
} from "./panel.js";

/**
 * Safe wrapper around ruTorrent's CategoryList to avoid crashes when settings/selection
 * are not yet initialized (null/undefined).
 */
export class CategoryList {
  constructor(props) {
    Object.assign(this, props);

    this.selection = null;
    this.searches = [];
    this.views = [];
    this.quickSearch = {
      search: { val: "" },
      debounce: { timeoutId: 0, delayMs: 220 },
    };
    this.statistic = CategoryListStatistic.from("pview", this.viewSelections, {
      pstate: [
        (_, torrent) => [
          // Main group (Completed / Downloading / Stopped)
          torrent.done >= 1000
            ? "-_-_-com-_-_-" // Completed (even if error)
            : torrent.state & this.dStatus.paused
            ? "-_-_-wfa-_-_-" // Truly paused (Stopped)
            : torrent.state & this.dStatus.started
            ? "-_-_-dls-_-_-" // Actively downloading (Started, not Paused)
            : "-_-_-wfa-_-_-", // Otherwise fallback to Stopped

          // Activity group (Active / Inactive)
          torrent.state & this.dStatus.started
            ? torrent.dl >= 1024 || torrent.ul >= 1024
              ? "-_-_-act-_-_-" // Active
              : "-_-_-iac-_-_-" // Inactive
            : null,
        ],
        {
          "-_-_-err-_-_-": (_, torrent) => torrent.state & this.dStatus.error,
        },
      ],
      plabel: [(_, torrent) => [torrent.label ? "clabel__" + torrent.label : "-_-_-nlb-_-_-"]],
      psearch: [
        (_, torrent) =>
          this.searches
            .map((_, searchIndex) => [_, searchIndex])
            .filter(([search]) => search.match(torrent.name))
            .map(([_, searchIndex]) => `psearch_${searchIndex}`),
        {
          quick_search: (_, torrent) => this.matchQuickSearch(torrent.name),
        },
      ],
    });
    this.refreshPanel = {
      pview: () => [
        this.updatedStatisticEntry("pview", "pview_all"),
        ...this.views.map((view, i) =>
          this.updatedStatisticEntry("pview", this.statistic.indexToViewId(i), {
            text: view.name,
            icon: view.name.charAt(0),
          })
        ),
      ],
      pstate: (attribs) => [...attribs.keys()].map((labelId) => this.updatedStatisticEntry("pstate", labelId)),
      plabel: (attribs) => [
        ...[...attribs.keys()]
          .filter((labelId) => !labelId.startsWith("clabel__"))
          .map((labelId) => this.updatedStatisticEntry("plabel", labelId)),
        ...[...this.torrentLabelTree.torrentLabels.keys()]
          .map((torrentLabel) => ["clabel__" + torrentLabel, torrentLabel])
          .map(([labelId, torrentLabel]) =>
            this.updatedStatisticEntry(
              "plabel",
              labelId,
              this.torrentLabelTree.prefixSuffix(
                torrentLabel,
                !this.settings?.["webui.show_label_path_tree"],
                !this.settings?.["webui.show_empty_path_labels"]
              ) ?? {},
              torrentLabel
            )
          ),
      ],
      psearch: () => [
        this.updatedStatisticEntry("psearch", "psearch_all"),
        ...this.searches.map((search, i) =>
          this.updatedStatisticEntry("psearch", `psearch_${i}`, {
            text: search.text,
            icon: "search",
          })
        ),
      ],
    };
    this.configured = false;
  }

  config(settings) {
    if (this.configured) {
      console.warn("Category list is only configured once");
      return;
    }
    this.settings = settings || {};
    // Gracefully handle missing settings
    const requiredKeys = [
      "webui.closed_panels",
      "webui.category_panels",
      "webui.open_tegs.last",
      "webui.selected_labels.keep",
      "webui.selected_labels.last",
      "webui.selected_labels.views",
    ];
    if (!requiredKeys.every((key) => key in this.settings)) {
      console.warn("CategoryList: settings incomplete, falling back to defaults");
      this.settings = {
        "webui.closed_panels": {},
        "webui.category_panels": ["pview", "pstate", "plabel", "psearch"],
        "webui.open_tegs.last": [],
        "webui.selected_labels.keep": false,
        "webui.selected_labels.last": {},
        "webui.selected_labels.views": [],
        "webui.show_label_path_tree": true,
        "webui.show_empty_path_labels": true,
        "webui.show_viewlabelsize": true,
        "webui.show_statelabelsize": true,
        "webui.show_searchlabelsize": true,
        "webui.show_labelsize": true,
        "webui.open_tegs.keep": false,
      };
    }
    if (this.settings["webui.open_tegs.keep"]) {
      for (const text of this.settings["webui.open_tegs.last"]) {
        this.addTextSearch(text, true);
      }
    }
    const configChanged = this.updatePanels();
    const mapLegacyActLbls = (actLbls) =>
      Object.fromEntries(
        Object.entries(actLbls || {}).map(([panelId, labelIds]) => [
          mapLegacyPanelId(panelId),
          labelIds.map(mapLegacyLabelId),
        ])
      );

    // Restore selection from settings
    this.selection = PanelLabelSelection.fromConfig(
      this.settings["webui.selected_labels.keep"]
        ? mapLegacyActLbls(this.settings["webui.selected_labels.last"] || {})
        : {},
      this.sortedPanelIds
    );
    // Init views
    this.views = (this.settings["webui.selected_labels.views"] || []).map((view) => ({
      name: view.name,
      selection: PanelLabelSelection.fromConfig(
        mapLegacyActLbls(view.labels || {}),
        this.statistic.panelIds
      ),
    }));
    this.selection.adjustViewToCurrent(this.viewSelections, this.statistic.panelIds);
    this.statistic.viewSelections = this.viewSelections;

    this.configured = true;
    if (configChanged) {
      this.onConfigChangeFn();
    }
    this.syncFn();
  }

  updatedStatisticEntry(panelId, labelId, attrs, titleText) {
    attrs = { ...this.panelLabelAttribs[panelId].get(labelId), ...attrs };
    const bucket =
      labelId === `${panelId}_all`
        ? this.statistic
        : this.statistic.lookup(panelId, labelId);
    const sizeString = this.byteSizeToStringFn(bucket.size);
    return [
      labelId,
      {
        ...attrs,
        count: String(bucket.count),
        size: this.showSize(panelId) && bucket.size > 0 ? sizeString : null,
        title: `${titleText || attrs.text} (${bucket.count} ; ${sizeString})`,
        selected: this.selection ? this.selection.active(panelId, labelId) : false,
      },
    ];
  }

  get viewSelections() {
    return this.views.map((view) => view.selection);
  }

  onViewsChange() {
    this.settings["webui.selected_labels.views"] = this.views.map((view) => ({
      name: view.name,
      labels: view.selection.toConfig(),
    }));
    this.selection?.adjustCurrentToView(this.viewSelections, this.statistic.panelIds);
    this.statistic.viewSelections = this.viewSelections;
    this.rescan("pview");
    this.refresh("pview");
    this.onSelectionChange("pview");
  }

  selectionActive(panelId, labelId) {
    return this.selection ? this.selection.active(panelId, labelId) : false;
  }

  showSize(panelId) {
    return (
      {
        pview: this.settings?.["webui.show_viewlabelsize"],
        pstate: this.settings?.["webui.show_statelabelsize"],
        psearch: this.settings?.["webui.show_searchlabelsize"],
      }[panelId] ?? this.settings?.["webui.show_labelsize"]
    );
  }

  get sortedPanelIds() {
    return (this.settings?.["webui.category_panels"] || [])
      .map(mapLegacyPanelId)
      .filter((panelId) => panelId in this.panelAttribs);
  }

  updatePanels() {
    const oldPanelIds = new Set(this.sortedPanelIds);
    const newPanelIds = Object.keys(this.panelAttribs).filter(
      (panelId) => !oldPanelIds.has(panelId)
    );
    if (newPanelIds.length) {
      const panels = this.sortedPanelIds.concat(newPanelIds);
      if (this.settings)
        this.settings["webui.category_panels"] = panels;
    }
    this.panelAttribs = Object.fromEntries(
      this.sortedPanelIds.map((panelId) => [panelId, this.panelAttribs[panelId]])
    );
    this.panelLabelAttribs = Object.fromEntries(
      this.sortedPanelIds.map((panelId) => [panelId, this.panelLabelAttribs[panelId]])
    );
    for (const [panelId, attribs] of Object.entries(this.panelAttribs)) {
      attribs.closed = this.panelClosed(panelId);
    }
    return newPanelIds.length > 0;
  }

  addPanel(panelId, name, labelAttribs, statisticInit) {
    if (panelId in this.panelAttribs) return;
    this.panelAttribs[panelId] = { name, closed: this.panelClosed(panelId) };
    this.panelLabelAttribs[panelId] = labelAttribs;
    this.statistic.addPanel(
      panelId,
      labelAttribs,
      statisticInit ?? (() => this.statistic.lookup(panelId, labelAttribs.keys().next().value))
    );
  }

  panelClosed(panelId) {
    return this.settings?.["webui.closed_panels"]?.[panelId] ?? false;
  }

  togglePanel(panelId) {
    const closed = !(this.panelAttribs[panelId]?.closed ?? false);
    this.panelAttribs[panelId].closed = closed;
    if (this.settings)
      this.settings["webui.closed_panels"][panelId] = closed;
    this.rescan(panelId);
    this.refresh(panelId);
    this.onPanelChange(panelId);
  }

  updatedPanel(panelId) {
    const attribs = this.panelAttribs[panelId];
    return [
      panelId,
      {
        ...attribs,
        name: this.panelAttribs[panelId].name,
        type: panelId,
        labelIds: this.labelIds(panelId),
      },
    ];
  }

  labelIds(panelId) {
    return [...this.panelLabelAttribs[panelId].keys()];
  }

  setPanels(panelAttribs, panelLabelAttribs) {
    this.panelAttribs = panelAttribs;
    this.panelLabelAttribs = panelLabelAttribs;
    this.updatePanels();
  }

  get panelAttribsEntries() {
    return Object.entries(this.panelAttribs).map(([panelId, attribs]) => [
      panelId,
      { ...attribs },
    ]);
  }

  rescan(panelId) {
    const attribs = this.statistic.lookupPanelId(panelId);
    attribs?.clear();
    this.statistic.scan(this.torrents.values());
  }

  refresh(panelId) {
    if (!this.statistic.lookupPanelId(panelId)) return;
    const panel = this.updatedPanel(panelId);
    const labels = this.refreshPanel[panelId](this.statistic.lookupPanelId(panelId));
    this.renderFn(panel, labels);
  }

  refreshPanels(panelIds = this.sortedPanelIds) {
    panelIds.forEach((panelId) => this.refresh(panelId));
  }

  renderLabel(panelId, labelId) {
    if (!this.statistic.lookupPanelId(panelId)) return;
    const attribs = this.statistic.lookup(panelId, labelId);
    this.renderFn(this.updatedPanel(panelId), [
      this.updatedStatisticEntry(panelId, labelId, undefined, attribs.text),
    ]);
  }

  updatedTorrentLabelTree(labelTree) {
    this.torrentLabelTree = TorrentLabelTree.from(labelTree);
    this.refreshPanels(["plabel"]);
  }

  addTextSearch(searchText, skipRender) {
    if (!searchText || searchText === "") return;
    this.searches.push(new TextSearch(searchText));
    if (this.settings)
      this.settings["webui.open_tegs.last"] = this.searches.map((s) => s.text);
    if (!skipRender) {
      this.rescan("psearch");
      this.refreshPanels(["psearch", "pview"]);
    }
  }

  deleteTextSearch(searchIndex) {
    this.searches.splice(searchIndex, 1);
    if (this.settings)
      this.settings["webui.open_tegs.last"] = this.searches.map((s) => s.text);
    this.rescan("psearch");
    this.refreshPanels(["psearch", "pview"]);
  }

  changeQuickSearch(text) {
    this.quickSearch.search.val = text;
    this.rescan("psearch");
    this.refreshPanels(["psearch", "pview"]);
  }

  updateTorrents(newTorrents, added = [], deleted = []) {
    const idmap = new Map(this.torrents);
    added.forEach((id) => idmap.set(id, newTorrents.get(id)));
    deleted.forEach((id) => idmap.delete(id));
    this.torrents = idmap;
    this.rescan();
    this.refresh();
  }

  refreshAll() {
    this.rescan();
    this.refresh();
  }

  rescan(panelId) {
    (panelId ? [panelId] : this.sortedPanelIds).forEach((pid) => this.rescan(pid));
  }

  refresh(panelId) {
    (panelId ? [panelId] : this.sortedPanelIds).forEach((pid) => this.refresh(pid));
  }

  onSelectionChange(panelId) {
    // noop placeholder
  }

  onPanelChange(panelId) {
    // noop placeholder
  }

  onConfigChangeFn() {}
  syncFn() {}

  isLabelIdSelected(panelId, labelId) {
    return this.selection ? this.selection.active(panelId, labelId) : false;
  }

  matchQuickSearch(name) {
    const val = this.quickSearch.search.val?.toLowerCase() || "";
    return !val || name.toLowerCase().includes(val);
  }
}

function mapLegacyPanelId(panelId) {
  const legacyPanelIdMap = {
    trackers: "pstate",
    labels: "plabel",
    search: "psearch",
  };
  return legacyPanelIdMap[panelId] || panelId;
}

function mapLegacyLabelId(labelId) {
  const legacyLabelIdMap = {
    trackers: { all: "pstate_all" },
  };
  return legacyLabelIdMap[labelId]?.[labelId] || labelId;
}
