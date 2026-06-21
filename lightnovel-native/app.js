// Native Single Page Application controller for LightNovel - Arabic Version
document.addEventListener("DOMContentLoaded", () => {
  // Global Configurations
  const CONFIG = {
    apiBase: "http://localhost:5000/api"
  };

  // User Session State
  const userState = {
    token: localStorage.getItem("ln_token") || null,
    username: localStorage.getItem("ln_username") || null,
    role: localStorage.getItem("ln_role") || "Free",
    vipExpiresAt: localStorage.getItem("ln_vip_expires_at") || null
  };

  // Application View State
  const state = {
    currentView: "home",
    selectedNovelId: null,
    selectedChapterId: null,
    bookmarks: JSON.parse(localStorage.getItem("ln_bookmarks") || "[]"),
    history: JSON.parse(localStorage.getItem("ln_history") || "[]"),
    activePopularRange: "weekly",
    activeAdminTab: "novels",
    autoScrollInterval: null,
    readerSettings: JSON.parse(
      localStorage.getItem("ln_reader_settings") ||
      JSON.stringify({
        theme: "dark",
        fontSize: "medium",
        fontFamily: "serif"
      })
    ),
    activeSearchFilters: {
      query: "",
      status: "all",
      type: "all",
      order: "latest",
      genres: []
    }
  };

  // DOM Elements cache
  const elements = {
    navHome: document.getElementById("nav-home"),
    navBookmarks: document.getElementById("nav-bookmarks"),
    navSearch: document.getElementById("nav-search"),
    navAccount: document.getElementById("nav-account"),
    navAdmin: document.getElementById("nav-admin"),
    searchBar: document.getElementById("search-bar"),
    btnSurprise: document.getElementById("btn-surprise"),

    viewHome: document.getElementById("view-home"),
    viewDetails: document.getElementById("view-details"),
    viewReader: document.getElementById("view-reader"),
    viewBookmarks: document.getElementById("view-bookmarks"),
    viewSearch: document.getElementById("view-search"),
    viewAccount: document.getElementById("view-account"),
    viewAdmin: document.getElementById("view-admin"),

    // Floating reader settings
    settingsBtn: document.getElementById("reader-settings-btn"),
    settingsPanel: document.getElementById("reader-settings-panel"),
    progressBar: document.getElementById("reading-progress-bar")
  };

  // Initialize App
  function init() {
    setupEventListeners();
    applyReaderSettings();
    updateAuthUI();
    handleRouting();
  }

  // --- API SERVICE FETCHERS (WITH MOCK FALLBACKS) ---

  async function getNovels(filters = {}) {
    if (CONFIG.apiBase) {
      try {
        const queryParams = new URLSearchParams();
        if (filters.status && filters.status !== "all") queryParams.append("status", filters.status);
        if (filters.type && filters.type !== "all") queryParams.append("type", filters.type);
        if (filters.order) queryParams.append("order", filters.order);
        if (filters.query) queryParams.append("query", filters.query);
        if (filters.genres && filters.genres.length > 0) queryParams.append("genres", filters.genres.join(","));

        const res = await fetch(`${CONFIG.apiBase}/novels?${queryParams.toString()}`);
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn("Backend API offline. Falling back to local mock data.", err);
      }
    }

    // Mock Fallback Filter Logic
    let results = [...window.NOVELS_DATA];
    if (filters.query) {
      const q = filters.query.toLowerCase().trim();
      results = results.filter(n => n.title.toLowerCase().includes(q) || n.altTitle.toLowerCase().includes(q) || n.author.toLowerCase().includes(q));
    }
    if (filters.status && filters.status !== "all") {
      results = results.filter(n => n.status === filters.status);
    }
    if (filters.type && filters.type !== "all") {
      results = results.filter(n => n.type === filters.type);
    }
    if (filters.genres && filters.genres.length > 0) {
      results = results.filter(n => filters.genres.every(g => n.genres.includes(g)));
    }
    if (filters.order === "rating") {
      results.sort((a, b) => b.rating - a.rating);
    } else if (filters.order === "views") {
      results.sort((a, b) => b.views - a.views);
    } else if (filters.order === "az") {
      results.sort((a, b) => a.title.localeCompare(b.title));
    } else {
      results.sort((a, b) => new Date(b.updatedOn) - new Date(a.updatedOn));
    }
    return results;
  }

  async function getNovelDetail(novelId) {
    if (CONFIG.apiBase) {
      try {
        const res = await fetch(`${CONFIG.apiBase}/novels/${novelId}`);
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn("Backend API offline. Falling back to local details.", err);
      }
    }
    return window.NOVELS_DATA.find(n => n.id === novelId);
  }

  async function getChapter(chapterId) {
    if (CONFIG.apiBase) {
      try {
        const headers = {};
        if (userState.token) {
          headers["Authorization"] = `Bearer ${userState.token}`;
        }
        const res = await fetch(`${CONFIG.apiBase}/chapters/${chapterId}`, { headers });
        if (res.ok) {
          return await res.json();
        } else if (res.status === 403) {
          const errData = await res.json();
          return { error: errData.error, is_locked: true };
        }
      } catch (err) {
        console.warn("Backend API offline. Loading mock chapter.", err);
      }
    }

    // Mock Fallback Chapter Fetch
    let foundCh = null;
    let foundNovel = null;
    window.NOVELS_DATA.forEach(n => {
      n.volumes.forEach(v => {
        v.chapters.forEach(c => {
          if (c.id === chapterId) {
            foundCh = { ...c, novel_title: n.title, novel_id: n.id, volume_number: v.volumeNumber };
            foundNovel = n;
          }
        });
      });
    });

    if (foundCh && foundCh.is_locked) {
      // Mock authorization lock check
      if (userState.role !== "VIP" && userState.role !== "Admin") {
        return {
          error: "🔒 هذا الفصل مقفل للأعضاء المشتركين VIP فقط. يرجى تسجيل الدخول والاشتراك لتتمكن من القراءة.",
          is_locked: true
        };
      }
    }

    return foundCh;
  }

  async function getAds() {
    if (CONFIG.apiBase) {
      try {
        const headers = {};
        if (userState.token) {
          headers["Authorization"] = `Bearer ${userState.token}`;
        }
        const res = await fetch(`${CONFIG.apiBase}/ads`, { headers });
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn(err);
      }
    }
    // Mock Default Ads for free users
    if (userState.role === "VIP" || userState.role === "Admin") {
      return {};
    }
    return {
      header: '<div style="background:rgba(99,102,241,0.1); border:1px dashed var(--primary); padding:1rem; text-align:center; border-radius:8px; margin-bottom:2rem;"><p style="color:var(--primary); font-weight:bold;">مساحة إعلانية علوية </p></div>',
      sidebar: '<div style="background:rgba(168,85,247,0.1); border:1px dashed var(--secondary); padding:2rem 1rem; text-align:center; border-radius:12px;"><p style="color:var(--secondary); font-weight:bold;">إعلان جانبي ممول</p></div>',
      reader: '<div style="background:rgba(244,63,94,0.1); border:1px dashed var(--accent); padding:1.5rem; text-align:center; border-radius:8px; margin:2rem 0;"><p style="color:var(--accent); font-weight:bold;">إعلان داخل القارئ (ادعمنا بالاشتراك لإزالته)</p></div>'
    };
  }

  // Event Listeners setup
  function setupEventListeners() {
    // Navigation Routing
    elements.navHome.onclick = () => navigate("home");
    elements.navBookmarks.onclick = () => navigate("bookmarks");
    elements.navSearch.onclick = () => navigate("search");
    elements.navAccount.onclick = () => navigate("account");
    elements.navAdmin.onclick = () => navigate("admin");

    // Global Search Bar
    elements.searchBar.addEventListener("input", (e) => {
      state.activeSearchFilters.query = e.target.value;
      if (state.currentView !== "search") {
        navigate("search");
      } else {
        renderSearchPage();
      }
    });

    elements.btnSurprise.onclick = surpriseMe;
    window.addEventListener("hashchange", handleRouting);

    // Reading Settings Sidebar Toggle
    elements.settingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isVisible = elements.settingsPanel.style.display === "block";
      elements.settingsPanel.style.display = isVisible ? "none" : "block";
    });

    document.addEventListener("click", () => {
      elements.settingsPanel.style.display = "none";
    });

    elements.settingsPanel.addEventListener("click", (e) => e.stopPropagation());
    window.addEventListener("scroll", updateReadingProgress);
    setupSettingsButtons();

    // Sidebar Popular range switcher
    document.querySelectorAll(".popular-tab-btn").forEach(btn => {
      if (btn.closest(".popular-block")) {
        btn.addEventListener("click", () => {
          document.querySelectorAll(".popular-block .popular-tab-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          state.activePopularRange = btn.getAttribute("data-range");
          renderPopularList(state.activePopularRange);
        });
      }
    });

    // Account view events
    setupAccountViewEvents();

    // Admin view events
    setupAdminViewEvents();
  }

  // Routing Handler
  function handleRouting() {
    // Clear auto-scroll when changing views
    if (state.autoScrollInterval) {
      clearInterval(state.autoScrollInterval);
      state.autoScrollInterval = null;
      const scrollToggleBtn = document.getElementById("btn-autoscroll-toggle");
      if (scrollToggleBtn) {
        scrollToggleBtn.textContent = "بدء التمرير";
        scrollToggleBtn.classList.remove("active");
      }
    }

    const hash = window.location.hash || "#home";
    const parts = hash.split("?");
    const view = parts[0].substring(1);
    const params = parseQueryParams(parts[1]);

    state.currentView = view;

    // Reset views visibility
    Object.values(elements).forEach(el => {
      if (el && el.classList && el.classList.contains("view-section")) {
        el.classList.remove("active");
      }
    });

    // Reset Nav highlights
    elements.navHome.classList.remove("active");
    elements.navBookmarks.classList.remove("active");
    elements.navSearch.classList.remove("active");
    elements.navAccount.classList.remove("active");
    elements.navAdmin.classList.remove("active");

    elements.settingsBtn.style.display = "none";
    elements.progressBar.style.width = "0%";

    if (view === "home") {
      elements.navHome.classList.add("active");
      elements.viewHome.classList.add("active");
      renderHomePage();
      renderPopularList(state.activePopularRange);
      updateHistorySidebar();
    } else if (view === "bookmarks") {
      elements.navBookmarks.classList.add("active");
      elements.viewBookmarks.classList.add("active");
      renderBookmarksPage();
    } else if (view === "search") {
      elements.navSearch.classList.add("active");
      elements.viewSearch.classList.add("active");
      renderSearchPage();
    } else if (view === "account") {
      elements.navAccount.classList.add("active");
      elements.viewAccount.classList.add("active");
      renderAccountPage();
    } else if (view === "admin") {
      if (!["Admin", "Publisher", "Translator", "Reviewer"].includes(userState.role)) {
        navigate("home");
        return;
      }
      elements.navAdmin.classList.add("active");
      elements.viewAdmin.classList.add("active");
      renderAdminPage();
    } else if (view === "details" && params.id) {
      state.selectedNovelId = params.id;
      elements.viewDetails.classList.add("active");
      renderDetailPage(params.id);
    } else if (view === "reader" && params.chapter) {
      state.selectedChapterId = params.chapter;
      elements.viewReader.classList.add("active");
      elements.settingsBtn.style.display = "flex";
      renderReaderPage(params.chapter);
    } else {
      window.location.hash = "#home";
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
    renderGlobalAds();
  }

  function navigate(view, params = {}) {
    let hash = `#${view}`;
    const query = Object.entries(params)
      .map(([k, v]) => `${k}=${v}`)
      .join("&");
    if (query) hash += `?${query}`;
    window.location.hash = hash;
  }

  function parseQueryParams(queryString) {
    if (!queryString) return {};
    return queryString.split("&").reduce((acc, pair) => {
      const [k, v] = pair.split("=");
      acc[k] = decodeURIComponent(v);
      return acc;
    }, {});
  }

  function surpriseMe() {
    getNovels().then(novels => {
      if (novels.length === 0) return;
      const idx = Math.floor(Math.random() * novels.length);
      navigate("details", { id: novels[idx].id });
    });
  }

  // --- ADS RENDERING ---
  async function renderGlobalAds() {
    const ads = await getAds();

    // Header ad
    let headerContainer = document.getElementById("header-ad-placeholder");
    if (ads.header) {
      if (!headerContainer) {
        headerContainer = document.createElement("div");
        headerContainer.id = "header-ad-placeholder";
        headerContainer.className = "container";
        elements.viewHome.parentNode.insertBefore(headerContainer, elements.viewHome);
      }
      headerContainer.innerHTML = ads.header;
      headerContainer.style.display = "block";
    } else if (headerContainer) {
      headerContainer.style.display = "none";
    }

    // Sidebar ad
    const sidebarAdPlaceholder = document.getElementById("sidebar-ad-placeholder");
    if (sidebarAdPlaceholder) {
      sidebarAdPlaceholder.innerHTML = ads.sidebar || "";
      sidebarAdPlaceholder.style.display = ads.sidebar ? "block" : "none";
    }
  }

  // --- HOME PAGE RENDER ---
  async function renderHomePage() {
    const novels = await getNovels();
    const featured = novels[0];

    const sliderContainer = document.getElementById("slider-placeholder");
    if (featured) {
      sliderContainer.innerHTML = `
        <div class="slider-container">
          <div class="slider-image">
            <img src="${featured.cover}" alt="${featured.title}">
          </div>
          <div class="slider-info">
            <span class="slider-tag">الأكثر شعبية هذا الأسبوع</span>
            <h2 class="slider-title">${featured.title}</h2>
            <p class="slider-synopsis">${featured.synopsis}</p>
            <div class="slider-meta">
              <div class="meta-item">النوع: <strong>${featured.type}</strong></div>
              <div class="meta-item">الحالة: <strong>${featured.status}</strong></div>
              <div class="meta-item">التقييم: <strong>★ ${featured.rating}</strong></div>
            </div>
            <div class="slider-actions">
              <button class="btn-primary" id="btn-read-featured">
                <svg width="18" height="18" fill="white" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
                اقرأ الآن
              </button>
              <button class="btn-outline" id="btn-details-featured">تفاصيل الرواية</button>
            </div>
          </div>
        </div>
      `;

      document.getElementById("btn-read-featured").onclick = () => {
        navigate("details", { id: featured.id });
      };

      document.getElementById("btn-details-featured").onclick = () => {
        navigate("details", { id: featured.id });
      };
    }

    const hotGrid = document.getElementById("hot-updates-grid");
    hotGrid.innerHTML = novels.map(novel => {
      let chapterLinksHTML = '';
      const latestChs = novel.latest_chapters || [];
      if (latestChs.length > 0) {
        chapterLinksHTML = `
          <div class="card-chapters-links">
            ${latestChs.map(ch => `
              <a href="#reader?chapter=${ch.id}" class="card-chapter-link" data-ch-id="${ch.id}">
                <span>الفصل ${ch.chapter_number}: ${ch.title}</span>
                <span style="font-size:0.75rem; color:var(--text-muted);">مجلد ${ch.volume_number}</span>
              </a>
            `).join("")}
          </div>
        `;
      } else {
        chapterLinksHTML = `<p class="card-update-time">لا توجد فصول متوفرة</p>`;
      }

      return `
        <div class="novel-card" data-id="${novel.id}">
          <div class="card-image-wrap">
            <img src="${novel.cover}" alt="${novel.title}">
            <span class="card-badge">${novel.type}</span>
            <span class="card-rating">★ ${novel.rating}</span>
          </div>
          <div class="card-content">
            <div>
              <h3 class="card-title">${novel.title}</h3>
            </div>
            ${chapterLinksHTML}
          </div>
        </div>
      `;
    }).join("");

    hotGrid.querySelectorAll(".novel-card").forEach(card => {
      card.onclick = (e) => {
        const link = e.target.closest(".card-chapter-link");
        if (link) {
          e.preventDefault();
          e.stopPropagation();
          const chId = link.getAttribute("data-ch-id");
          navigate("reader", { chapter: chId });
          return;
        }
        const id = card.getAttribute("data-id");
        navigate("details", { id });
      };
    });
  }

  async function renderPopularList(range) {
    const placeholder = document.getElementById("popular-list-placeholder");
    if (!placeholder) return;

    let novels = await getNovels({ order: range === "weekly" ? "views" : "rating" });
    const top5 = novels.slice(0, 5);

    placeholder.innerHTML = top5.map((novel, idx) => `
      <div class="popular-item" data-id="${novel.id}">
        <div class="popular-rank">#${idx + 1}</div>
        <div class="popular-thumb">
          <img src="${novel.cover}" alt="${novel.title}">
        </div>
        <div class="popular-info">
          <div class="popular-title">${novel.title}</div>
          <div class="popular-meta">★ ${novel.rating} • ${novel.views.toLocaleString()} مشاهدة</div>
        </div>
      </div>
    `).join("") + '<div id="sidebar-ad-placeholder" class="sidebar-block" style="margin-top:2rem;"></div>';

    placeholder.querySelectorAll(".popular-item").forEach(item => {
      item.onclick = () => {
        const id = item.getAttribute("data-id");
        navigate("details", { id });
      };
    });

    renderGlobalAds();
  }

  // --- DETAIL PAGE RENDER ---
  async function renderDetailPage(novelId) {
    const novel = await getNovelDetail(novelId);
    if (!novel) {
      elements.viewDetails.innerHTML = `<div class="container text-center"><p class="my-5">الرواية غير موجودة.</p></div>`;
      return;
    }

    const isBookmarked = state.bookmarks.includes(novel.id);

    elements.viewDetails.innerHTML = `
      <div class="novel-detail-header">
        <div class="container novel-detail-layout">
          <div class="detail-image">
            <img src="${novel.cover}" alt="${novel.title}">
          </div>
          <div class="detail-info">
            <h1>${novel.title}</h1>
            <p class="detail-alt">${novel.alt_title || novel.altTitle}</p>
            
            <div class="detail-stats">
              <div class="stat-box">
                <div class="stat-val">★ ${novel.rating}</div>
                <div class="stat-lbl">التقييم</div>
              </div>
              <div class="stat-box">
                <div class="stat-val">${novel.views.toLocaleString()}</div>
                <div class="stat-lbl">المشاهدات</div>
              </div>
              <div class="stat-box">
                <div class="stat-val" id="detail-follow-count">${novel.followers.toLocaleString()}</div>
                <div class="stat-lbl">المتابعين</div>
              </div>
            </div>

            <div class="detail-actions">
              <button class="btn-primary" id="btn-detail-start-reading">
                اقرأ الفصل الأول
              </button>
              <button class="btn-bookmark ${isBookmarked ? 'active' : ''}" id="btn-detail-bookmark">
                <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M17 3H7c-1.1 0-1.99.9-1.99 2L5 21l7-3 7 3V5c0-1.1-.9-2-2-2z"/></svg>
                <span>${isBookmarked ? 'في المحفوظات' : 'إضافة للمحفوظات'}</span>
              </button>
            </div>

            <div class="detail-meta-grid">
              <div class="meta-field">
                <span class="meta-label">المؤلف</span>
                <span class="meta-value">${novel.author}</span>
              </div>
              <div class="meta-field">
                <span class="meta-label">الرسام</span>
                <span class="meta-value">${novel.artist}</span>
              </div>
              <div class="meta-field">
                <span class="meta-label">الحالة</span>
                <span class="meta-value">${novel.status}</span>
              </div>
              <div class="meta-field">
                <span class="meta-label">النوع</span>
                <span class="meta-value">${novel.type}</span>
              </div>
              <div class="meta-field">
                <span class="meta-label">اللغة الأصلية</span>
                <span class="meta-value">${novel.native_language || novel.nativeLanguage}</span>
              </div>
              <div class="meta-field">
                <span class="meta-label">سنة الإصدار</span>
                <span class="meta-value">${novel.released}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="container">
        <div class="detail-synopsis-section">
          <h2 class="section-title">القصة / Synopsis</h2>
          <p class="synopsis-text">${novel.synopsis}</p>
          <div class="genres-selector" style="margin-top: 1.5rem;">
            ${novel.genres.map(g => `<span class="genre-tag" style="cursor: default;">${g}</span>`).join("")}
          </div>
        </div>

        <div class="detail-chapters-section">
          <h2 class="section-title">المجلدات والفصول</h2>
          ${novel.volumes.map(vol => `
            <div class="volume-card">
              <div class="volume-header">
                المجلد ${vol.volumeNumber}: ${vol.title}
                <span style="font-size: 0.85rem; font-weight: normal; color: var(--text-muted);">${vol.chapters.length} فصول</span>
              </div>
              <div class="volume-chapters-list">
                ${vol.chapters.map(ch => `
                  <div class="chapter-row" data-ch-id="${ch.id}">
                    <span class="chapter-name">
                      الفصل ${ch.chapterNumber}: ${ch.title}
                      ${ch.is_locked ? '<span style="color:var(--accent); font-size:0.8rem; margin-right:0.5rem;">🔒 مميز VIP</span>' : ''}
                    </span>
                    <span class="chapter-date">${ch.releaseDate}</span>
                  </div>
                `).join("")}
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;

    document.getElementById("btn-detail-start-reading").onclick = () => {
      const firstCh = novel.volumes[0]?.chapters[0];
      if (firstCh) navigate("reader", { chapter: firstCh.id });
    };

    const bkmkBtn = document.getElementById("btn-detail-bookmark");
    bkmkBtn.onclick = async () => {
      if (CONFIG.apiBase && userState.token) {
        try {
          const res = await fetch(`${CONFIG.apiBase}/bookmarks/toggle`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": `Bearer ${userState.token}`
            },
            body: JSON.stringify({ novel_id: novel.id })
          });
          if (res.ok) {
            const data = await res.json();
            const index = state.bookmarks.indexOf(novel.id);
            if (data.status === "added") {
              if (index === -1) state.bookmarks.push(novel.id);
            } else {
              if (index > -1) state.bookmarks.splice(index, 1);
            }
            localStorage.setItem("ln_bookmarks", JSON.stringify(state.bookmarks));
          }
        } catch (e) {
          console.error(e);
        }
      } else {
        // Local Toggle
        const idx = state.bookmarks.indexOf(novel.id);
        if (idx > -1) {
          state.bookmarks.splice(idx, 1);
        } else {
          state.bookmarks.push(novel.id);
        }
        localStorage.setItem("ln_bookmarks", JSON.stringify(state.bookmarks));
      }

      const isNowBookmarked = state.bookmarks.includes(novel.id);
      bkmkBtn.classList.toggle("active", isNowBookmarked);
      bkmkBtn.querySelector("span").textContent = isNowBookmarked ? 'في المحفوظات' : 'إضافة للمحفوظات';

      const countEl = document.getElementById("detail-follow-count");
      let count = novel.followers + (isNowBookmarked ? 1 : -1);
      countEl.textContent = Math.max(0, count).toLocaleString();
    };

    elements.viewDetails.querySelectorAll(".chapter-row").forEach(row => {
      row.onclick = () => {
        const chId = row.getAttribute("data-ch-id");
        navigate("reader", { chapter: chId });
      };
    });
  }

  // --- BOOKMARKS PAGE RENDER ---
  async function renderBookmarksPage() {
    const bkmkGrid = document.getElementById("bookmarks-grid");
    let bookmarkedNovels = [];

    if (CONFIG.apiBase && userState.token) {
      try {
        const res = await fetch(`${CONFIG.apiBase}/bookmarks`, {
          headers: { "Authorization": `Bearer ${userState.token}` }
        });
        if (res.ok) {
          bookmarkedNovels = await res.json();
          // Sync local storage ids
          state.bookmarks = bookmarkedNovels.map(n => n.id);
          localStorage.setItem("ln_bookmarks", JSON.stringify(state.bookmarks));
        }
      } catch (e) {
        console.error(e);
      }
    }

    if (bookmarkedNovels.length === 0) {
      // Local Fallback Load
      const allNovels = await getNovels();
      bookmarkedNovels = allNovels.filter(n => state.bookmarks.includes(n.id));
    }

    if (bookmarkedNovels.length === 0) {
      bkmkGrid.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1;">
          <svg viewBox="0 0 24 24"><path d="M17 3H7c-1.1 0-1.99.9-1.99 2L5 21l7-3 7 3V5c0-1.1-.9-2-2-2zm0 15l-5-2.18L7 18V5h10v13z"/></svg>
          <h3>لا توجد روايات محفوظة</h3>
          <p>توجه لصفحة تفاصيل الرواية واضغط على زر "إضافة للمحفوظات" لتظهر روايتك هنا.</p>
        </div>
      `;
      return;
    }

    bkmkGrid.innerHTML = bookmarkedNovels.map(novel => `
      <div class="novel-card" data-id="${novel.id}">
        <div class="card-image-wrap">
          <img src="${novel.cover}" alt="${novel.title}">
          <span class="card-badge">${novel.type}</span>
          <span class="card-rating">★ ${novel.rating}</span>
        </div>
        <div class="card-content">
          <div>
            <h3 class="card-title">${novel.title}</h3>
            <p class="card-update-time">الحالة: ${novel.status}</p>
          </div>
          <button class="btn-outline btn-bookmark-delete" style="width:100%; margin-top:1rem; padding: 0.5rem; font-size:0.85rem;" data-id="${novel.id}">
            إزالة من المحفوظات
          </button>
        </div>
      </div>
    `).join("");

    bkmkGrid.querySelectorAll(".novel-card").forEach(card => {
      card.onclick = async (e) => {
        if (e.target.classList.contains("btn-bookmark-delete")) {
          e.stopPropagation();
          const id = e.target.getAttribute("data-id");

          if (CONFIG.apiBase && userState.token) {
            await fetch(`${CONFIG.apiBase}/bookmarks/toggle`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${userState.token}`
              },
              body: JSON.stringify({ novel_id: id })
            });
          }
          const index = state.bookmarks.indexOf(id);
          if (index > -1) state.bookmarks.splice(index, 1);
          localStorage.setItem("ln_bookmarks", JSON.stringify(state.bookmarks));
          renderBookmarksPage();
          return;
        }
        const id = card.getAttribute("data-id");
        navigate("details", { id });
      };
    });
  }

  // --- SEARCH AND FILTER PAGE RENDER ---
  async function renderSearchPage() {
    const viewSearch = elements.viewSearch;

    if (!viewSearch.querySelector(".search-filter-panel")) {
      const novels = await getNovels();
      const allGenres = Array.from(new Set(novels.flatMap(n => n.genres)));

      viewSearch.innerHTML = `
        <div class="container">
          <h2 class="section-title">البحث المتقدم والفلاتر</h2>
          <div class="search-filter-panel">
            <div class="filter-group">
              <label class="settings-label">حالة الرواية</label>
              <select id="filter-status">
                <option value="all">الكل</option>
                <option value="مستمرة">مستمرة</option>
                <option value="مكتملة">مكتملة</option>
              </select>
            </div>

            <div class="filter-group">
              <label class="settings-label">نوع العمل</label>
              <select id="filter-type">
                <option value="all">الكل</option>
                <option value="رواية خفيفة">رواية خفيفة (LN)</option>
                <option value="رواية ويب">رواية ويب (WN)</option>
              </select>
            </div>

            <div class="filter-group">
              <label class="settings-label">الترتيب حسب</label>
              <select id="filter-order">
                <option value="latest">آخر التحديثات</option>
                <option value="rating">الأعلى تقييماً</option>
                <option value="views">الأكثر قراءة</option>
                <option value="az">أبجدي (أ-ي)</option>
              </select>
            </div>

            <div class="filter-group" style="grid-column: 1 / -1;">
              <label class="settings-label">التصنيفات (اختيار متعدد)</label>
              <div class="genres-selector" id="genres-filter-list">
                ${allGenres.map(g => `<span class="genre-tag" data-genre="${g}">${g}</span>`).join("")}
              </div>
            </div>
          </div>

          <div class="cards-grid" id="search-results-grid"></div>
        </div>
      `;

      document.getElementById("filter-status").onchange = (e) => {
        state.activeSearchFilters.status = e.target.value;
        applyFilters();
      };
      document.getElementById("filter-type").onchange = (e) => {
        state.activeSearchFilters.type = e.target.value;
        applyFilters();
      };
      document.getElementById("filter-order").onchange = (e) => {
        state.activeSearchFilters.order = e.target.value;
        applyFilters();
      };

      viewSearch.querySelectorAll("#genres-filter-list .genre-tag").forEach(tag => {
        tag.onclick = () => {
          const genre = tag.getAttribute("data-genre");
          tag.classList.toggle("selected");

          const index = state.activeSearchFilters.genres.indexOf(genre);
          if (index > -1) {
            state.activeSearchFilters.genres.splice(index, 1);
          } else {
            state.activeSearchFilters.genres.push(genre);
          }
          applyFilters();
        };
      });
    }

    applyFilters();
  }

  async function applyFilters() {
    const results = await getNovels(state.activeSearchFilters);
    const grid = document.getElementById("search-results-grid");

    if (results.length === 0) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1;">
          <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
          <h3>لا توجد نتائج مطابقة</h3>
          <p>جرب تصفية البحث باستخدام معايير أخرى.</p>
        </div>
      `;
      return;
    }

    grid.innerHTML = results.map(novel => `
      <div class="novel-card" data-id="${novel.id}">
        <div class="card-image-wrap">
          <img src="${novel.cover}" alt="${novel.title}">
          <span class="card-badge">${novel.type}</span>
          <span class="card-rating">★ ${novel.rating}</span>
        </div>
        <div class="card-content">
          <div>
            <h3 class="card-title">${novel.title}</h3>
            <p class="card-update-time">الحالة: ${novel.status}</p>
          </div>
          <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem; text-align: left;">
            تحديث: ${novel.updated_on || novel.updatedOn}
          </div>
        </div>
      </div>
    `).join("");

    grid.querySelectorAll(".novel-card").forEach(card => {
      card.onclick = () => {
        const id = card.getAttribute("data-id");
        navigate("details", { id });
      };
    });
  }

  // --- READER PAGE RENDER ---
  async function renderReaderPage(chapterId) {
    const chapter = await getChapter(chapterId);

    if (!chapter) {
      elements.viewReader.innerHTML = `<div class="container text-center"><p class="my-5">الفصل غير موجود.</p></div>`;
      return;
    }

    if (chapter.is_locked && chapter.error) {
      // Access Forbidden / Premium Lock Screen
      elements.viewReader.innerHTML = `
        <div class="reader-container text-center" style="max-width:650px; margin:4rem auto; padding:3rem; background:var(--bg-card); border:1px solid var(--border-color); border-radius:16px;">
          <div style="font-size:4rem; margin-bottom:1.5rem;">🔒</div>
          <h2 style="font-weight:800; font-size:1.6rem; color:var(--accent); margin-bottom:1rem;">محتوى مغلق للأعضاء VIP</h2>
          <p style="color:var(--text-muted); line-height:1.6; margin-bottom:2rem;">
            هذا الفصل من رواية <strong>"${chapter.novel_title || ''}"</strong> مخصص لأعضاء الباقة المميزة فقط لدعم جهود الترجمة والنشر.
          </p>
          <button class="btn-primary" style="margin:0 auto;" id="btn-reader-lock-go-account">تسجيل الدخول / ترقية حسابي إلى VIP 🌟</button>
        </div>
      `;
      document.getElementById("btn-reader-lock-go-account").onclick = () => navigate("account");
      return;
    }

    // Success Loading Chapter
    let prevCh = null;
    let nextCh = null;

    // Fetch and sync novels data to locate prev/next
    const novels = await getNovels();
    const flatChapters = [];

    for (const novel of novels) {
      const detailed = await getNovelDetail(novel.id);
      if (detailed && detailed.volumes) {
        detailed.volumes.forEach(vol => {
          vol.chapters.forEach(ch => {
            flatChapters.push({ novel: detailed, vol, ch });
          });
        });
      }
    }

    const index = flatChapters.findIndex(item => item.ch.id === chapterId);
    if (index > -1) {
      const match = flatChapters[index];
      if (index > 0 && flatChapters[index - 1].novel.id === match.novel.id) {
        prevCh = flatChapters[index - 1].ch;
      }
      if (index < flatChapters.length - 1 && flatChapters[index + 1].novel.id === match.novel.id) {
        nextCh = flatChapters[index + 1].ch;
      }
    }

    addToHistory(chapter);

    // Get active reader ads
    const ads = await getAds();

    // Calculate word count & estimated reading time
    const cleanText = chapter.content.replace(/<[^>]*>/g, '').trim();
    const wordCount = cleanText ? cleanText.split(/\s+/).length : 0;
    const readingTime = Math.max(1, Math.round(wordCount / 180));

    elements.viewReader.innerHTML = `
      <div class="reader-container">
        <div class="reader-header">
          <div class="reader-breadcrumb">
            <a href="#details?id=${chapter.novel_id}">${chapter.novel_title || ''}</a> / المجلد ${chapter.volume_number}
          </div>
          <h1 class="reader-title">الفصل ${chapter.chapter_number}: ${chapter.title}</h1>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.5rem; display: flex; align-items: center; justify-content: center; gap: 1rem;">
            <span>⏱️ وقت القراءة المتوقع: ${readingTime} دقيقة</span>
            <span>📝 عدد الكلمات: ${wordCount.toLocaleString()} كلمة</span>
          </div>
        </div>

        <div class="reader-nav">
          <button class="btn-outline" id="btn-prev-chapter" ${!prevCh ? 'disabled style="opacity:0.5; cursor:default;"' : ''}>← السابق</button>
          <button class="btn-outline" id="btn-next-chapter" ${!nextCh ? 'disabled style="opacity:0.5; cursor:default;"' : ''}>التالي →</button>
        </div>

        <div class="reader-content-wrap">
          ${ads.reader ? `<div class="reader-ad-top">${ads.reader}</div>` : ''}
          <div class="reader-content">
            ${chapter.content}
          </div>
          ${ads.reader ? `<div class="reader-ad-bottom">${ads.reader}</div>` : ''}
        </div>

        <div class="reader-nav" style="margin-top: 4rem;">
          <button class="btn-outline" id="btn-prev-chapter-bottom" ${!prevCh ? 'disabled style="opacity:0.5; cursor:default;"' : ''}>← السابق</button>
          <button class="btn-primary" id="btn-back-to-novel">صفحة الرواية</button>
          <button class="btn-outline" id="btn-next-chapter-bottom" ${!nextCh ? 'disabled style="opacity:0.5; cursor:default;"' : ''}>التالي →</button>
        </div>
      </div>
    `;

    if (prevCh) {
      document.getElementById("btn-prev-chapter").onclick = () => navigate("reader", { chapter: prevCh.id });
      document.getElementById("btn-prev-chapter-bottom").onclick = () => navigate("reader", { chapter: prevCh.id });
    }
    if (nextCh) {
      document.getElementById("btn-next-chapter").onclick = () => navigate("reader", { chapter: nextCh.id });
      document.getElementById("btn-next-chapter-bottom").onclick = () => navigate("reader", { chapter: nextCh.id });
    }

    document.getElementById("btn-back-to-novel").onclick = () => {
      navigate("details", { id: chapter.novel_id });
    };

    applyReaderCustomStyle();

    // Enable interactive paragraph comments
    const contentContainer = elements.viewReader.querySelector(".reader-content");
    if (contentContainer) {
      const paragraphs = contentContainer.querySelectorAll("p");
      paragraphs.forEach((p, idx) => {
        p.classList.add("reader-paragraph");

        const badge = document.createElement("span");
        badge.className = "paragraph-comment-badge";
        // Simulate varying number of comments per paragraph
        const count = idx % 3 === 0 ? Math.floor(Math.random() * 8) + 1 : 0;
        badge.innerHTML = `💬 ${count}`;
        p.appendChild(badge);

        badge.onclick = (e) => {
          e.stopPropagation();
          alert(`التعليقات على الفقرة رقم ${idx + 1} ستتوفر قريباً في التحديث القادم مع دعم المحادثات الحية!`);
        };
      });
    }
  }

  // --- BOOKMARKS & HISTORY BACKEND SYNC ---
  async function addToHistory(chapter) {
    if (CONFIG.apiBase && userState.token) {
      try {
        await fetch(`${CONFIG.apiBase}/history`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${userState.token}`
          },
          body: JSON.stringify({ novel_id: chapter.novel_id, chapter_id: chapter.id })
        });
      } catch (e) {
        console.error(e);
      }
    }

    // Local Update
    state.history = state.history.filter(h => h.novelId !== chapter.novel_id);
    state.history.unshift({
      novelId: chapter.novel_id,
      novelTitle: chapter.novel_title,
      novelCover: "", // will load on render
      chapterId: chapter.id,
      chapterNumber: chapter.chapter_number,
      chapterTitle: chapter.title,
      timestamp: new Date().toISOString()
    });
    if (state.history.length > 5) state.history.pop();
    localStorage.setItem("ln_history", JSON.stringify(state.history));
    updateHistorySidebar();
  }

  async function updateHistorySidebar() {
    const listContainer = document.getElementById("history-list-placeholder");
    if (!listContainer) return;

    let historyList = [];
    if (CONFIG.apiBase && userState.token) {
      try {
        const res = await fetch(`${CONFIG.apiBase}/history`, {
          headers: { "Authorization": `Bearer ${userState.token}` }
        });
        if (res.ok) {
          const apiHistory = await res.json();
          historyList = apiHistory.map(h => ({
            novelId: h.novel_id,
            novelTitle: h.novel_title,
            novelCover: h.novel_cover,
            chapterId: h.chapter_id,
            chapterNumber: h.chapter_number,
            chapterTitle: h.chapter_title,
            timestamp: h.timestamp
          }));
          // Sync local
          state.history = historyList;
          localStorage.setItem("ln_history", JSON.stringify(state.history));
        }
      } catch (e) {
        console.error(e);
      }
    }

    if (historyList.length === 0) {
      historyList = state.history;
    }

    if (historyList.length === 0) {
      listContainer.innerHTML = `
        <div style="font-size: 0.85rem; color: var(--text-muted); text-align: center; padding: 1.5rem 0;">
          لا يوجد سجل تصفح.
        </div>
      `;
      return;
    }

    listContainer.innerHTML = historyList.map(item => `
      <div class="history-item" style="cursor: pointer;" data-ch-id="${item.chapterId}">
        <div class="history-thumb">
          <img src="${item.novelCover || 'assets/shadow_alchemist.png'}" alt="${item.novelTitle}">
        </div>
        <div class="history-info">
          <div class="history-title">${item.novelTitle}</div>
          <div class="history-chapter">فصل ${item.chapterNumber}: ${item.chapterTitle}</div>
          <div class="history-time">${timeAgo(item.timestamp)}</div>
        </div>
      </div>
    `).join("");

    listContainer.querySelectorAll(".history-item").forEach(el => {
      el.onclick = () => {
        const chId = el.getAttribute("data-ch-id");
        navigate("reader", { chapter: chId });
      };
    });
  }

  // --- ACCOUNT VIEW & PROFILE INTERACTION ---
  function setupAccountViewEvents() {
    let authMode = "login"; // 'login' or 'register'

    const loginTab = document.getElementById("btn-auth-tab-login");
    const registerTab = document.getElementById("btn-auth-tab-register");
    const emailGroup = document.getElementById("auth-email-group");
    const submitBtn = document.getElementById("btn-auth-submit");
    const authForm = document.getElementById("auth-form");
    const errorMsg = document.getElementById("auth-error-msg");

    loginTab.onclick = () => {
      authMode = "login";
      loginTab.classList.add("active");
      registerTab.classList.remove("active");
      emailGroup.style.display = "none";
      submitBtn.textContent = "تسجيل الدخول";
      errorMsg.style.display = "none";
    };

    registerTab.onclick = () => {
      authMode = "register";
      registerTab.classList.add("active");
      loginTab.classList.remove("active");
      emailGroup.style.display = "block";
      submitBtn.textContent = "إنشاء حساب";
      errorMsg.style.display = "none";
    };

    authForm.onsubmit = async (e) => {
      e.preventDefault();
      errorMsg.style.display = "none";

      const username = document.getElementById("auth-username").value;
      const email = document.getElementById("auth-email").value;
      const password = document.getElementById("auth-password").value;

      const endpoint = authMode === "login" ? "/auth/login" : "/auth/register";
      const payload = { username, password };
      if (authMode === "register") payload.email = email;

      if (!CONFIG.apiBase) {
        // Mock Auth simulation
        if (authMode === "login" && username === "admin") {
          saveSession("mock-token-admin", "admin", "Admin", null);
        } else {
          saveSession("mock-token-user", username, "Free", null);
        }
        renderAccountPage();
        return;
      }

      try {
        const res = await fetch(CONFIG.apiBase + endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (res.ok) {
          saveSession(data.token, data.username, data.role, data.vip_expires_at);
          renderAccountPage();
          if (data.role === "Admin") navigate("admin");
          else navigate("home");
        } else {
          errorMsg.textContent = data.error || "حدث خطأ ما";
          errorMsg.style.display = "block";
        }
      } catch (err) {
        errorMsg.textContent = "عذراً، خادم الباك اند غير متصل.";
        errorMsg.style.display = "block";
      }
    };

    document.getElementById("btn-logout").onclick = () => {
      clearSession();
      navigate("home");
    };

    document.getElementById("btn-profile-go-admin").onclick = () => navigate("admin");

    document.getElementById("btn-subscribe-vip").onclick = async () => {
      if (!CONFIG.apiBase) {
        // Mock upgrade
        saveSession(userState.token, userState.username, "VIP", Date.now() + 86400 * 30 * 1000);
        renderAccountPage();
        return;
      }

      try {
        const res = await fetch(`${CONFIG.apiBase}/subscribe`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${userState.token}`
          },
          body: JSON.stringify({ payment_token: "MOCK_PAYMENT_SUCCESS_" + Date.now() })
        });
        if (res.ok) {
          const data = await res.json();
          saveSession(data.token, userState.username, "VIP", data.vip_expires_at);
          renderAccountPage();
        }
      } catch (e) {
        console.error(e);
      }
    };
  }

  function saveSession(token, username, role, vipExpiresAt) {
    userState.token = token;
    userState.username = username;
    userState.role = role;
    userState.vipExpiresAt = vipExpiresAt;

    localStorage.setItem("ln_token", token);
    localStorage.setItem("ln_username", username);
    localStorage.setItem("ln_role", role);
    if (vipExpiresAt) localStorage.setItem("ln_vip_expires_at", vipExpiresAt);
    else localStorage.removeItem("ln_vip_expires_at");

    updateAuthUI();
  }

  function clearSession() {
    userState.token = null;
    userState.username = null;
    userState.role = "Free";
    userState.vipExpiresAt = null;

    localStorage.removeItem("ln_token");
    localStorage.removeItem("ln_username");
    localStorage.removeItem("ln_role");
    localStorage.removeItem("ln_vip_expires_at");

    updateAuthUI();
  }

  function updateAuthUI() {
    const hasAdmin = userState.token && ["Admin", "Publisher", "Translator", "Reviewer"].includes(userState.role);
    elements.navAdmin.style.display = hasAdmin ? "block" : "none";
    elements.navAdmin.textContent = userState.role === "Admin" ? "لوحة المدير" : "لوحة العمل";

    const goAdminBtn = document.getElementById("btn-profile-go-admin");
    if (goAdminBtn) {
      goAdminBtn.style.display = hasAdmin ? "block" : "none";
      goAdminBtn.textContent = userState.role === "Admin" ? "لوحة تحكم المدير" : "لوحة تحكم العمل";
    }

    const label = userState.token ? `حسابي (${userState.username})` : "حسابي";
    elements.navAccount.textContent = label;
  }

  function renderAccountPage() {
    const authSub = document.getElementById("account-auth-subview");
    const profileSub = document.getElementById("account-profile-subview");

    if (userState.token) {
      authSub.style.display = "none";
      profileSub.style.display = "block";

      document.getElementById("profile-username").textContent = userState.username;

      let roleLabel = "نوع الحساب: مجاني (تظهر إعلانات)";
      if (userState.role === "VIP") roleLabel = "نوع الحساب: VIP المميز (بدون إعلانات) 🌟";
      if (userState.role === "Translator") roleLabel = "نوع الحساب: مترجم معتمد ✍️";
      if (userState.role === "Publisher") roleLabel = "نوع الحساب: ناشر محتوى 📁";
      if (userState.role === "Reviewer") roleLabel = "نوع الحساب: مدقق ومحرر فصول 🔍";
      if (userState.role === "Admin") roleLabel = "نوع الحساب: مدير النظام 🛠️";
      document.getElementById("profile-role").textContent = roleLabel;

      const vipText = document.getElementById("profile-vip-status");
      const subBtn = document.getElementById("btn-subscribe-vip");

      if (userState.role === "VIP") {
        const exp = userState.vipExpiresAt ? new Date(parseFloat(userState.vipExpiresAt) * 1000).toLocaleDateString("ar-EG") : "غير محدد";
        vipText.innerHTML = `اشتراكك VIP نشط حالياً وينتهي في: <strong>${exp}</strong>. شكراً لدعمك لنا!`;
        subBtn.style.display = "none";
      } else if (["Admin", "Publisher", "Translator", "Reviewer"].includes(userState.role)) {
        vipText.innerHTML = "أنت تمتلك صلاحيات العمل والمساهمة في ترجمة وتدقيق ونشر الروايات.";
        subBtn.style.display = "none";
      } else {
        vipText.innerHTML = "أنت تستخدم الحساب المجاني حالياً. قم بالترقية للوصول للفصول المغلقة وإلغاء كافة الإعلانات تماماً.";
        subBtn.style.display = "block";
      }
    } else {
      authSub.style.display = "block";
      profileSub.style.display = "none";
    }
  }

  // --- ADMIN VIEW & SUBVIEWS INTERACTION ---
  function setupAdminViewEvents() {
    const tabs = ["novels", "chapters", "assignments", "users", "ads", "audit"];
    tabs.forEach(t => {
      const btn = document.getElementById(`admin-menu-${t}`);
      if (btn) {
        btn.onclick = () => {
          tabs.forEach(x => {
            const menuBtn = document.getElementById(`admin-menu-${x}`);
            const subView = document.getElementById(`admin-subview-${x}`);
            if (menuBtn) menuBtn.classList.remove("active");
            if (subView) subView.style.display = "none";
          });
          btn.classList.add("active");
          const targetSubview = document.getElementById(`admin-subview-${t}`);
          if (targetSubview) targetSubview.style.display = "block";
          state.activeAdminTab = t;
          if (t === "chapters") populateNovelDropdown();
          if (t === "assignments") populateNovelDropdownForAssign();
          if (t === "users") loadUsersList();
          if (t === "ads") loadAdsSettings();
          if (t === "audit") loadAuditLogsList(1);
        };
      }
    });

    // Admin Submit Novel Form
    document.getElementById("admin-novel-form").onsubmit = async (e) => {
      e.preventDefault();
      const payload = {
        id: document.getElementById("admin-novel-id").value,
        title: document.getElementById("admin-novel-title").value,
        alt_title: document.getElementById("admin-novel-alt").value,
        cover: document.getElementById("admin-novel-cover").value,
        author: document.getElementById("admin-novel-author").value,
        artist: document.getElementById("admin-novel-artist").value,
        type: document.getElementById("admin-novel-type").value,
        status: document.getElementById("admin-novel-status").value,
        native_language: document.getElementById("admin-novel-lang").value,
        released: document.getElementById("admin-novel-year").value,
        genres: document.getElementById("admin-novel-genres").value.split(",").map(g => g.trim()).filter(Boolean),
        synopsis: document.getElementById("admin-novel-synopsis").value
      };

      try {
        const res = await fetch(`${CONFIG.apiBase}/admin/novels`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${userState.token}`
          },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          alert("تم حفظ ونشر الرواية بنجاح!");
          document.getElementById("admin-novel-form").reset();
        } else {
          const err = await res.json();
          alert("خطأ: " + err.error);
        }
      } catch (e) {
        alert("فشل الإرسال للباك اند");
      }
    };

    // Admin Submit Chapter Form
    document.getElementById("admin-chapter-form").onsubmit = async (e) => {
      e.preventDefault();
      const payload = {
        novel_id: document.getElementById("admin-chapter-novel-select").value,
        volume_number: parseInt(document.getElementById("admin-chapter-vol-num").value),
        volume_title: document.getElementById("admin-chapter-vol-title").value,
        chapter_number: parseInt(document.getElementById("admin-chapter-num").value),
        title: document.getElementById("admin-chapter-title").value,
        is_locked: document.getElementById("admin-chapter-locked").checked,
        content: document.getElementById("admin-chapter-content").value
      };

      try {
        const res = await fetch(`${CONFIG.apiBase}/admin/chapters`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${userState.token}`
          },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          alert("تم نشر الفصل بنجاح بنجاح!");
          document.getElementById("admin-chapter-form").reset();
        } else {
          const err = await res.json();
          alert("خطأ: " + err.error);
        }
      } catch (e) {
        alert("فشل الإرسال للباك اند");
      }
    };

    // Admin Ads Submit Form
    document.getElementById("admin-ads-form").onsubmit = async (e) => {
      e.preventDefault();
      const zones = ["header", "sidebar", "reader"];
      let success = true;
      for (const zone of zones) {
        const ad_code = document.getElementById(`admin-ad-${zone}`).value;
        try {
          const res = await fetch(`${CONFIG.apiBase}/admin/ads`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": `Bearer ${userState.token}`
            },
            body: JSON.stringify({ zone, ad_code, is_active: true })
          });
          if (!res.ok) success = false;
        } catch {
          success = false;
        }
      }
      if (success) alert("تم حفظ أكواد الإعلانات بنجاح!");
      else alert("حدث خطأ أثناء حفظ بعض الإعلانات.");
    };

    // Admin/Publisher Assign Team Form
    const assignForm = document.getElementById("admin-assign-form");
    if (assignForm) {
      assignForm.onsubmit = async (e) => {
        e.preventDefault();
        const payload = {
          novel_id: document.getElementById("admin-assign-novel-select").value,
          user_id: parseInt(document.getElementById("admin-assign-user-id").value),
          role: document.getElementById("admin-assign-role").value
        };

        try {
          const res = await fetch(`${CONFIG.apiBase}/admin/assign`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": `Bearer ${userState.token}`
            },
            body: JSON.stringify(payload)
          });
          if (res.ok) {
            alert("تم تعيين عضو الفريق بنجاح!");
            assignForm.reset();
            loadAssignmentsList();
          } else {
            const err = await res.json();
            alert("خطأ: " + err.error);
          }
        } catch {
          alert("فشل إرسال طلب التعيين");
        }
      };
    }
  }

  async function populateNovelDropdown() {
    const select = document.getElementById("admin-chapter-novel-select");
    const novels = await getNovels();
    select.innerHTML = novels.map(n => `<option value="${n.id}">${n.title}</option>`).join("");
  }

  async function populateNovelDropdownForAssign() {
    const select = document.getElementById("admin-assign-novel-select");
    if (!select) return;
    const novels = await getNovels();
    select.innerHTML = novels.map(n => `<option value="${n.id}">${n.title}</option>`).join("");
    // Refresh assignments list when selected novel changes
    select.onchange = () => {
      loadAssignmentsList();
    };
    loadAssignmentsList();
  }

  function escapeHTML(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  async function loadAssignmentsList() {
    const select = document.getElementById("admin-assign-novel-select");
    const tableBody = document.getElementById("admin-assignments-table-body");
    if (!select || !tableBody) return;
    const novelId = select.value;
    if (!novelId) {
      tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem;">يرجى اختيار رواية أولاً</td></tr>';
      return;
    }
    tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem;">جاري التحميل...</td></tr>';
    try {
      const res = await fetch(`${CONFIG.apiBase}/admin/assignments/${novelId}`, {
        headers: { "Authorization": `Bearer ${userState.token}` }
      });
      if (res.ok) {
        const list = await res.json();
        if (list.length === 0) {
          tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1.5rem; color:var(--text-muted);">لا يوجد فريق عمل معين لهذه الرواية بعد.</td></tr>';
          return;
        }
        tableBody.innerHTML = list.map(item => `
          <tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:0.8rem;">${escapeHTML(item.username)}</td>
            <td style="padding:0.8rem;">${escapeHTML(item.email)}</td>
            <td style="padding:0.8rem;"><span style="background:var(--primary); color:white; padding:0.2rem 0.5rem; border-radius:4px; font-size:0.8rem;">${escapeHTML(item.role)}</span></td>
          </tr>
        `).join("");
      } else {
        tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem; color:var(--accent);">حدث خطأ أثناء جلب فريق العمل.</td></tr>';
      }
    } catch {
      tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem; color:var(--accent);">فشل الاتصال بالخادم.</td></tr>';
    }
  }

  async function loadAuditLogsList(page = 1) {
    const tableBody = document.getElementById("admin-audit-table-body");
    const pagContainer = document.getElementById("audit-pagination");
    if (!tableBody) return;
    tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem;">جاري جلب سجل العمليات...</td></tr>';
    try {
      const res = await fetch(`${CONFIG.apiBase}/admin/audit?page=${page}&per_page=15`, {
        headers: { "Authorization": `Bearer ${userState.token}` }
      });
      if (res.ok) {
        const data = await res.json();
        const logs = data.items || [];
        if (logs.length === 0) {
          tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:var(--text-muted);">سجل العمليات الأمني فارغ.</td></tr>';
          pagContainer.innerHTML = "";
          return;
        }
        tableBody.innerHTML = logs.map(log => {
          const localTime = new Date(log.timestamp).toLocaleString("ar-EG");
          const username = escapeHTML(log.username || 'System');
          const action = escapeHTML(log.action);
          const details = escapeHTML(log.details || '');
          const ip = escapeHTML(log.ip_address || '');

          return `
            <tr style="border-bottom:1px solid var(--border-color); font-family:monospace;">
              <td style="padding:0.8rem; direction:ltr; text-align:right;">${localTime}</td>
              <td style="padding:0.8rem;"><strong>${username}</strong> (ID: ${log.user_id || 'N/A'})</td>
              <td style="padding:0.8rem; color:var(--primary); font-weight:bold;">${action}</td>
              <td style="padding:0.8rem;">${details}</td>
              <td style="padding:0.8rem; direction:ltr;">${ip}</td>
            </tr>
          `;
        }).join("");

        // Render pagination controls
        let pagHTML = "";
        for (let i = 1; i <= data.total_pages; i++) {
          pagHTML += `<button class="popular-tab-btn ${i === data.page ? 'active' : ''}" onclick="window.loadAuditLogsList(${i})">${i}</button>`;
        }
        pagContainer.innerHTML = pagHTML;
      }
    } catch {
      tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:var(--accent);">فشل تحميل سجل العمليات الأمني.</td></tr>';
    }
  }
  // Expose to window for pagination click handler
  window.loadAuditLogsList = loadAuditLogsList;

  async function loadUsersList() {
    const tableBody = document.getElementById("admin-users-table-body");
    tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem;">جاري التحميل...</td></tr>';

    try {
      const res = await fetch(`${CONFIG.apiBase}/admin/users`, {
        headers: { "Authorization": `Bearer ${userState.token}` }
      });
      if (res.ok) {
        const users = await res.json();
        tableBody.innerHTML = users.map(user => `
          <tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:0.8rem;">${user.id}</td>
            <td style="padding:0.8rem;">${user.username}</td>
            <td style="padding:0.8rem;">${user.email}</td>
            <td style="padding:0.8rem;"><strong>${user.role}</strong></td>
            <td style="padding:0.8rem; display:flex; gap:0.5rem; align-items:center;">
              <select id="user-role-${user.id}" style="padding:0.3rem; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); color:var(--text-main); border-radius:4px;">
                <option value="Free" ${user.role === 'Free' ? 'selected' : ''}>Free</option>
                <option value="VIP" ${user.role === 'VIP' ? 'selected' : ''}>VIP</option>
                <option value="Translator" ${user.role === 'Translator' ? 'selected' : ''}>Translator</option>
                <option value="Publisher" ${user.role === 'Publisher' ? 'selected' : ''}>Publisher</option>
                <option value="Reviewer" ${user.role === 'Reviewer' ? 'selected' : ''}>Reviewer</option>
                <option value="Admin" ${user.role === 'Admin' ? 'selected' : ''}>Admin</option>
              </select>
              <input type="number" id="user-vip-days-${user.id}" placeholder="أيام VIP" style="width:70px; padding:0.3rem; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); color:var(--text-main); border-radius:4px;" value="30">
              <button class="btn-primary btn-save-user-role" data-user-id="${user.id}" style="padding:0.35rem 0.8rem; font-size:0.8rem;">حفظ</button>
            </td>
          </tr>
        `).join("");

        tableBody.querySelectorAll(".btn-save-user-role").forEach(btn => {
          btn.onclick = async () => {
            const userId = btn.getAttribute("data-user-id");
            const role = document.getElementById(`user-role-${userId}`).value;
            const vip_days = parseInt(document.getElementById(`user-vip-days-${userId}`).value) || 0;

            try {
              const saveRes = await fetch(`${CONFIG.apiBase}/admin/users/${userId}/role`, {
                method: "PUT",
                headers: {
                  "Content-Type": "application/json",
                  "Authorization": `Bearer ${userState.token}`
                },
                body: JSON.stringify({ role, vip_days })
              });
              if (saveRes.ok) {
                alert("تم تحديث رتبة وصلاحيات العضو بنجاح!");
                loadUsersList();
              }
            } catch (err) {
              alert("فشل تحديث العضو");
            }
          };
        });
      }
    } catch {
      tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:var(--accent);">فشل الاتصال بالباك اند لجلب الأعضاء.</td></tr>';
    }
  }

  async function loadAdsSettings() {
    try {
      const res = await fetch(`${CONFIG.apiBase}/admin/ads`, {
        headers: { "Authorization": `Bearer ${userState.token}` }
      });
      if (res.ok) {
        const adsList = await res.json();
        adsList.forEach(ad => {
          const el = document.getElementById(`admin-ad-${ad.zone}`);
          if (el) el.value = ad.ad_code;
        });
      }
    } catch (e) {
      console.warn("Could not load ads settings from backend.");
    }
  }

  function renderAdminPage() {
    const role = userState.role;

    // Dynamically hide/show tabs based on roles
    const novelsMenu = document.getElementById("admin-menu-novels");
    const chaptersMenu = document.getElementById("admin-menu-chapters");
    const assignmentsMenu = document.getElementById("admin-menu-assignments");
    const usersMenu = document.getElementById("admin-menu-users");
    const adsMenu = document.getElementById("admin-menu-ads");
    const auditMenu = document.getElementById("admin-menu-audit");

    if (novelsMenu) novelsMenu.style.display = ["Admin", "Publisher"].includes(role) ? "block" : "none";
    if (chaptersMenu) chaptersMenu.style.display = ["Admin", "Publisher", "Translator", "Reviewer"].includes(role) ? "block" : "none";
    if (assignmentsMenu) assignmentsMenu.style.display = ["Admin", "Publisher"].includes(role) ? "block" : "none";
    if (usersMenu) usersMenu.style.display = (role === "Admin") ? "block" : "none";
    if (adsMenu) adsMenu.style.display = (role === "Admin") ? "block" : "none";
    if (auditMenu) auditMenu.style.display = (role === "Admin") ? "block" : "none";

    // Click the first permitted menu item to activate it
    const menus = [novelsMenu, chaptersMenu, assignmentsMenu, usersMenu, adsMenu, auditMenu];
    for (const m of menus) {
      if (m && m.style.display !== "none") {
        m.click();
        break;
      }
    }
  }

  // --- WRITER CONFIGURATION SETTINGS PANEL ---
  function setupSettingsButtons() {
    elements.settingsPanel.querySelectorAll(".theme-btn").forEach(btn => {
      btn.onclick = () => {
        const theme = btn.getAttribute("data-theme");
        state.readerSettings.theme = theme;
        saveReaderSettings();
        applyReaderSettings();
        applyReaderCustomStyle();
      };
    });

    elements.settingsPanel.querySelectorAll(".font-btn").forEach(btn => {
      btn.onclick = () => {
        const font = btn.getAttribute("data-font");
        state.readerSettings.fontFamily = font;
        saveReaderSettings();
        applyReaderSettings();
        applyReaderCustomStyle();
      };
    });

    elements.settingsPanel.querySelectorAll(".size-btn").forEach(btn => {
      btn.onclick = () => {
        const size = btn.getAttribute("data-size");
        state.readerSettings.fontSize = size;
        saveReaderSettings();
        applyReaderSettings();
        applyReaderCustomStyle();
      };
    });

    // Auto Scroll Setup
    const scrollToggleBtn = document.getElementById("btn-autoscroll-toggle");
    const scrollSpeedSelect = document.getElementById("select-autoscroll-speed");

    if (scrollToggleBtn && scrollSpeedSelect) {
      scrollToggleBtn.onclick = () => {
        if (state.autoScrollInterval) {
          // Stop auto-scrolling
          clearInterval(state.autoScrollInterval);
          state.autoScrollInterval = null;
          scrollToggleBtn.textContent = "بدء التمرير";
          scrollToggleBtn.classList.remove("active");
        } else {
          // Start auto-scrolling
          const speed = parseInt(scrollSpeedSelect.value) || 30;
          state.autoScrollInterval = setInterval(() => {
            window.scrollBy({ top: 1, behavior: "auto" });
          }, 1000 / speed);
          scrollToggleBtn.textContent = "إيقاف التمرير";
          scrollToggleBtn.classList.add("active");
        }
      };

      // Reset scroll interval if speed changes while running
      scrollSpeedSelect.onchange = () => {
        if (state.autoScrollInterval) {
          clearInterval(state.autoScrollInterval);
          const speed = parseInt(scrollSpeedSelect.value) || 30;
          state.autoScrollInterval = setInterval(() => {
            window.scrollBy({ top: 1, behavior: "auto" });
          }, 1000 / speed);
        }
      };
    }
  }

  function saveReaderSettings() {
    localStorage.setItem("ln_reader_settings", JSON.stringify(state.readerSettings));
  }

  function applyReaderSettings() {
    elements.settingsPanel.querySelectorAll(".theme-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-theme") === state.readerSettings.theme);
    });

    elements.settingsPanel.querySelectorAll(".font-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-font") === state.readerSettings.fontFamily);
    });

    elements.settingsPanel.querySelectorAll(".size-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-size") === state.readerSettings.fontSize);
    });
  }

  function applyReaderCustomStyle() {
    const content = document.querySelector(".reader-content");
    if (!content) return;

    let bg = "#111827";
    let text = "#F3F4F6";

    if (state.readerSettings.theme === "light") {
      bg = "#FFFFFF";
      text = "#111827";
    } else if (state.readerSettings.theme === "sepia") {
      bg = "#F4EAD4";
      text = "#5C4033";
    } else if (state.readerSettings.theme === "book") {
      bg = "#F5F5DC";
      text = "#2F4F4F";
    }

    const container = document.querySelector(".reader-container");
    if (container) {
      container.style.backgroundColor = bg;
      container.style.color = text;
      container.style.borderRadius = "12px";
      container.style.padding = "2rem";
      container.style.boxShadow = "0 4px 20px rgba(0,0,0,0.15)";
    }

    let fs = "19px";
    if (state.readerSettings.fontSize === "small") fs = "16px";
    if (state.readerSettings.fontSize === "large") fs = "23px";
    content.style.fontSize = fs;

    content.style.fontFamily = state.readerSettings.fontFamily === "serif" ? "var(--font-serif)" : "var(--font-sans)";
  }

  // --- SCROLLING PROGRESS BAR ---
  function updateReadingProgress() {
    if (state.currentView !== "reader") return;

    const winScroll = document.documentElement.scrollTop || document.body.scrollTop;
    const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
    const scrolled = height > 0 ? (winScroll / height) * 100 : 0;

    elements.progressBar.style.width = scrolled + "%";
  }

  // --- TIME AGO HELPER ---
  function timeAgo(dateString) {
    const now = new Date();
    const past = new Date(dateString);
    const msPerMinute = 60 * 1000;
    const msPerHour = msPerMinute * 60;
    const msPerDay = msPerHour * 24;

    const elapsed = now - past;

    if (elapsed < msPerMinute) {
      return "الآن";
    } else if (elapsed < msPerHour) {
      const mins = Math.round(elapsed / msPerMinute);
      return `منذ ${mins} دقيقة`;
    } else if (elapsed < msPerDay) {
      const hours = Math.round(elapsed / msPerHour);
      return `منذ ${hours} ساعة`;
    } else {
      const days = Math.round(elapsed / msPerDay);
      return `منذ ${days} يوم`;
    }
  }

  init();
});
