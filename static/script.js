let userLat = null, userLon = null, ready = false, loading = false;
let lastPlaces = [];
let favsDB = [];
let followReqPollTimer = null;

/* mood themes */
const moodThemes = {
  work: { accent: "#60a5fa", accent2: "#22d3ee", soft: "rgba(96,165,250,0.28)", bg: ["#0ea5e9", "#0f172a", "#22d3ee"], particle: "200,230,255" },
  date: { accent: "#a78bfa", accent2: "#fb7185", soft: "rgba(167,139,250,0.30)", bg: ["#a78bfa", "#1b0f2c", "#fb7185"], particle: "255,210,235" },
  quick_bite: { accent: "#22d3ee", accent2: "#38bdf8", soft: "rgba(34,211,238,0.28)", bg: ["#22d3ee", "#0b1220", "#38bdf8"], particle: "190,255,255" },
  budget: { accent: "#94a3b8", accent2: "#64748b", soft: "rgba(148,163,184,0.28)", bg: ["#94a3b8", "#0b1220", "#64748b"], particle: "215,225,235" }
};

function applyMoodTheme(mood) {
  const t = moodThemes[mood]; if (!t) return;
  document.documentElement.style.setProperty("--accent", t.accent);
  document.documentElement.style.setProperty("--accent2", t.accent2);
  document.documentElement.style.setProperty("--accent-soft", t.soft);
  document.documentElement.style.setProperty("--particle", t.particle);

  const grad = document.getElementById("moodGradient");
  if (grad) {
    grad.style.setProperty("--bg1", t.bg[0]);
    grad.style.setProperty("--bg2", t.bg[1]);
    grad.style.setProperty("--bg3", t.bg[2]);
  }

  const p = document.getElementById("particles");
  if (p) {
    p.style.opacity = "0.15";
    setTimeout(() => {
      initParticles();
      p.style.opacity = "0.55";
    }, 260);
  }
}

/* particles */
const canvas = document.getElementById("particles");
const ctx = canvas.getContext("2d");
let W = 0, H = 0, particles = [];

function resizeCanvas() {
  W = canvas.width = window.innerWidth;
  H = canvas.height = window.innerHeight;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

function initParticles() {
  particles = [];
  const count = Math.min(48, Math.floor((W * H) / 50000));
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 2 + 0.6,
      vx: (Math.random() - 0.5) * 0.18,
      vy: (Math.random() - 0.5) * 0.18,
      a: Math.random() * 0.22 + 0.10
    });
  }
}
initParticles();

function animateParticles() {
  ctx.clearRect(0, 0, W, H);
  ctx.globalCompositeOperation = "lighter";

  const rgb = getComputedStyle(document.documentElement).getPropertyValue("--particle").trim() || "200,230,255";

  for (const p of particles) {
    p.x += p.vx;
    p.y += p.vy;

    if (p.x < 0) p.x = W;
    if (p.x > W) p.x = 0;
    if (p.y < 0) p.y = H;
    if (p.y > H) p.y = 0;

    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${rgb},${p.a})`;
    ctx.fill();
  }

  requestAnimationFrame(animateParticles);
}
animateParticles();

/* =========================================================
   ✅ LOCATION (HARD FIX: works on Desktop + Mobile)
   ========================================================= */

const findBtn = document.getElementById("findBtn");
let lastGeoFixAt = 0;
let lastGeoAcc = null;

function setFindBtnState(disabled, label) {
  if (!findBtn) return;
  findBtn.disabled = disabled;
  if (label) findBtn.innerText = label;
}

function isLocationValid() {
  return userLat != null && userLon != null && isFinite(userLat) && isFinite(userLon);
}

function isLocationFresh() {
  const now = Date.now();
  const ageMs = now - lastGeoFixAt;

  if (!isLocationValid()) return false;

  // allow a bit more time on desktop
  if (ageMs > 20000) return false;

  // accuracy check (desktop can be high)
  if (lastGeoAcc != null && isFinite(lastGeoAcc)) {
    if (lastGeoAcc > 5000) return false;
  }

  return true;
}

async function forceGetLocationOnce(timeoutMs = 12000) {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(false);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        userLat = pos.coords.latitude;
        userLon = pos.coords.longitude;
        lastGeoFixAt = Date.now();
        lastGeoAcc = pos.coords.accuracy;
        ready = true;

        console.log("✅ getCurrentPosition location:", userLat, userLon, "acc:", lastGeoAcc);

        resolve(true);
      },
      (err) => {
        console.log("❌ getCurrentPosition failed:", err);
        resolve(false);
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: timeoutMs }
    );
  });
}

async function ensureFreshLocation(maxWaitMs = 9000) {
  const start = Date.now();

  if (isLocationFresh()) return true;

  // try immediate one-shot location request
  const ok = await forceGetLocationOnce(12000);
  if (ok && isLocationFresh()) return true;

  // else wait for watchPosition updates
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      if (isLocationFresh()) {
        clearInterval(timer);
        resolve(true);
        return;
      }

      if (Date.now() - start > maxWaitMs) {
        clearInterval(timer);
        resolve(false);
      }
    }, 200);
  });
}

if (findBtn) {
  setFindBtnState(true, "Getting location…");
  findBtn.onclick = findPlaces;

  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(
      (pos) => {
        userLat = pos.coords.latitude;
        userLon = pos.coords.longitude;
        lastGeoFixAt = Date.now();
        lastGeoAcc = pos.coords.accuracy;

        if (!ready) {
          ready = true;
          setFindBtnState(false, "Find nearby places");
        } else if (!loading) {
          setFindBtnState(false);
        }

        console.log("📍 watchPosition:", userLat, userLon, "acc:", lastGeoAcc);
      },
      (err) => {
        console.log("❌ watchPosition error:", err);
        ready = false;
        setFindBtnState(false, "Find nearby places"); // allow click to request GPS manually
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
  } else {
    setFindBtnState(false, "Find nearby places");
  }
}


/* time helper */
function pad2(n) { return String(n).padStart(2, "0"); }
function formatTime(dateObj) {
  let h = dateObj.getHours();
  let m = dateObj.getMinutes();
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12; if (h === 0) h = 12;
  return `${h}:${pad2(m)} ${ampm}`;
}

/* open status */
function getOpenStatus(place) {
  const oh = place.opening_hours;

  if (!oh || typeof window.opening_hours === "undefined") {
    return { unknown: true };
  }

  try {
    const lat = Number(place.lat);
    const lon = Number(place.lon);
    const ohObj = new window.opening_hours(oh, { lat, lon }, { tag_key: "opening_hours" });
    const now = new Date();
    const isOpen = ohObj.getState(now);
    const nextChange = ohObj.getNextChange(now);

    if (isOpen) {
      return { open: true, label: nextChange ? `Closes at ${formatTime(nextChange)}` : "Open now" };
    }
    return { open: false, label: nextChange ? `Opens at ${formatTime(nextChange)}` : "Closed now" };
  } catch {
    return { unknown: true };
  }
}

/* reach time estimate */
function reachTimes(distanceKm) {
  const km = Number(distanceKm);
  if (!isFinite(km)) return null;

  const scooterKmph = 22;
  const walkKmph = 4.5;

  const scooterMin = Math.max(2, Math.round((km / scooterKmph) * 60));
  const walkMin = Math.max(3, Math.round((km / walkKmph) * 60));

  return { scooterMin, walkMin };
}

/* AI explain */
function explainWhy(mood, place) {
  const cat = (place.category || "place").replaceAll("_", " ");
  const d = Number(place.distance);
  const nearWord = d < 1 ? "super close" : d < 2.5 ? "nearby" : "worth the short ride";

  if (mood === "date") {
    return `Perfect for <b>date</b> mood: cozy vibes + ${nearWord} + <b>${cat}</b> tag.`;
  }
  if (mood === "work") {
    return `Great for <b>work</b>: calm spot + ${nearWord} + ${cat} suitable for sitting.`;
  }
  if (mood === "quick_bite") {
    return `Best for <b>quick bite</b>: fast option + ${nearWord} + easy match (${cat}).`;
  }
  if (mood === "budget") {
    return `Good for <b>budget</b>: low-effort visit + ${nearWord} + popular ${cat}.`;
  }
  return `Recommended because it’s ${nearWord} and matches ${cat}.`;
}

/* favorites */
function placeStableId(p) {
  return p.place_id || `p_${p.name}_${p.lat}_${p.lon}`;
}

async function fetchFavoritesFromDB() {
  try {
    const r = await fetch("/api/favorites");
    const data = await r.json();
    favsDB = Array.isArray(data) ? data : [];
  } catch {
    favsDB = [];
  }
}

function isFav(p) {
  const id = placeStableId(p);
  return favsDB.some(x => x.place_id === id);
}

async function addFav(p) {
  await fetch("/api/favorites/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      place_id: placeStableId(p),
      name: p.name,
      category: p.category,
      lat: p.lat,
      lon: p.lon
    })
  });
  await fetchFavoritesFromDB();
}

async function removeFav(p) {
  await fetch("/api/favorites/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ place_id: placeStableId(p) })
  });
  await fetchFavoritesFromDB();
}

async function toggleFav(p) {
  if (isFav(p)) await removeFav(p);
  else await addFav(p);
  renderSavedGrouped();
  renderPlacesFiltered();
}

function classifyFav(p) {
  const cat = (p.category || "").toLowerCase();
  if (cat.includes("cafe")) return "⭐ Work Cafes";
  if (cat.includes("restaurant")) return "⭐ Date Spots";
  if (cat.includes("fast")) return "⭐ Quick Bites";
  return "⭐ Budget Food";
}

function renderSavedGrouped() {
  const wrap = document.getElementById("savedWrap");
  if (!wrap) return;

  if (!favsDB.length) {
    wrap.innerHTML = `
      <div class="savedSection">
        <div class="savedSectionTitle"><span>Saved places</span><span style="opacity:.65">Swipe →</span></div>
        <div class="carousel"><div style="opacity:.6;font-size:13px;padding:10px 2px;">No saved places yet.</div></div>
      </div>
    `;
    return;
  }

  const groups = {};
  favsDB.forEach(p => {
    const g = classifyFav(p);
    groups[g] = groups[g] || [];
    groups[g].push(p);
  });

  wrap.innerHTML = "";

  Object.keys(groups).forEach(groupName => {
    const section = document.createElement("div");
    section.className = "savedSection";
    section.innerHTML = `
      <div class="savedSectionTitle">
        <span>${groupName}</span>
        <span style="opacity:.65">Swipe →</span>
      </div>
      <div class="carousel"></div>
    `;

    const car = section.querySelector(".carousel");

    groups[groupName].slice(0, 10).forEach(p => {
      const card = document.createElement("div");
      card.className = "pinCard";
      card.innerHTML = `
        <div class="pinCardTitle">${p.name || "Saved place"}</div>
        <div class="pinCardMeta">${(p.category || "place").replaceAll("_", " ")}</div>
      `;
      card.onclick = () => openModal({
        place_id: p.place_id,
        name: p.name,
        category: p.category,
        distance: "Saved",
        lat: p.lat,
        lon: p.lon
      });
      car.appendChild(card);
    });

    wrap.appendChild(section);
  });
}

/* modal */
const modal = document.getElementById("modal");
const backdrop = document.getElementById("modalBackdrop");
const modalInner = document.getElementById("modalInner");

function openModal(place) {
  const pinned = isFav(place);

  const bbox = `${place.lon - 0.015},${place.lat - 0.01},${place.lon + 0.015},${place.lat + 0.01}`;
  const mapUrl = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${place.lat},${place.lon}`;

  modalInner.innerHTML = `
    <div class="modalTop">
      <div>
        <h2 class="modalTitle">${place.name}</h2>
        <div class="modalMeta">
          <span class="badge">${(place.distance === "Saved") ? "Saved" : `${place.distance} km`}</span>
          <span class="badge">${(place.category || "place").replaceAll("_", " ")}</span>
        </div>
      </div>

      <div style="display:flex; gap:10px;">
        <div class="iconBtn" id="modalPinBtn" title="Save">${pinned ? "★" : "☆"}</div>
        <div class="iconBtn" id="modalCloseBtn" title="Close">✕</div>
      </div>
    </div>

    <div class="modalBody">
      <div class="modalMap">
        <iframe loading="lazy" src="${mapUrl}"></iframe>
      </div>

      <div class="detailsBox">
        <h3 class="detailsTitle">Details</h3>
        <div class="detailsText">
          • <b>Category:</b> ${(place.category || "place").replaceAll("_", " ")} <br>
          • <b>Distance:</b> ${(place.distance === "Saved") ? "Saved" : `${place.distance} km`}
        </div>

        <div class="modalActions">
          <a class="linkBtn" target="_blank"
            href="https://www.google.com/maps?q=${place.lat},${place.lon}">
            Navigate →
          </a>
          <a class="linkBtn" target="_blank"
            href="https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=18/${place.lat}/${place.lon}">
            View on OSM →
          </a>
        </div>
      </div>
    </div>
  `;

  backdrop.classList.add("active");
  modal.classList.add("active");

  document.getElementById("modalCloseBtn").onclick = closeModal;
  document.getElementById("modalPinBtn").onclick = async () => {
    await toggleFav(place);
    document.getElementById("modalPinBtn").innerText = isFav(place) ? "★" : "☆";
  };
}

function closeModal() {
  backdrop.classList.remove("active");
  modal.classList.remove("active");
}
backdrop.addEventListener("click", closeModal);

/* search listener */
const searchInput = document.getElementById("searchInput");
if (searchInput) searchInput.addEventListener("input", () => renderPlacesFiltered());

function setSkeleton() {
  const results = document.getElementById("results");
  results.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const s = document.createElement("div");
    s.className = "skeleton";
    results.appendChild(s);
  }
}

function getFilteredPlaces() {
  const q = (document.getElementById("searchInput").value || "").toLowerCase().trim();
  let places = [...lastPlaces];

  if (q) {
    places = places.filter(p =>
      (p.name || "").toLowerCase().includes(q) ||
      (p.category || "").toLowerCase().includes(q)
    );
  }

  const set = new Set(favsDB.map(x => x.place_id));
  places.sort((a, b) => {
    const ap = set.has(placeStableId(a)) ? 1 : 0;
    const bp = set.has(placeStableId(b)) ? 1 : 0;
    if (bp !== ap) return bp - ap;
    return a.distance - b.distance;
  });

  return places;
}

function renderPlacesFiltered() {
  renderPlaces(getFilteredPlaces());
}

/* render cards */
function renderPlaces(data) {
  const results = document.getElementById("results");
  results.innerHTML = "";

  if (!data.length) {
    results.innerHTML = `<div style="opacity:.65; padding:50px 0; text-align:center;">No results found.</div>`;
    return;
  }

  const mood = document.getElementById("mood").value;

  data.forEach(p => {
    const pinned = isFav(p);
    const st = getOpenStatus(p);
    const times = reachTimes(p.distance);
    const why = explainWhy(mood, p);

    let statusHTML = "";
    if (!st.unknown) {
      statusHTML = `
        <span class="statusBadge">
          <span class="statusDot ${st.open ? "openDot" : "closedDot"}"></span>
          ${st.open ? "Open now" : "Closed now"}
        </span>
        <span class="badge">${st.label}</span>
      `;
    }

    let reachHTML = "";
    if (times) {
      reachHTML = `
        <div class="reachRow">
          🛵 <b>${times.scooterMin} min</b> &nbsp; • &nbsp; 🚶 <b>${times.walkMin} min</b>
        </div>
      `;
    }

    const card = document.createElement("div");
    card.className = "card";

    card.innerHTML = `
      <div class="actions">
        <div class="iconBtn pinBtn ${pinned ? "pinned" : ""}" title="Save">${pinned ? "★" : "☆"}</div>
        <div class="iconBtn openBtn" title="Open">↗</div>
      </div>

      <h3 class="cardTitle">${p.name}</h3>

      <div class="badgeRow">
        <span class="kmBadge"><span class="kmDot"></span>${p.distance} km</span>
        <span class="badge">${(p.category || "place").replaceAll("_", " ")}</span>
        ${statusHTML}
      </div>

      ${reachHTML}

      <div class="aiTag"><span class="aiDot"></span>AI Pick</div>
      <div class="aiLine">${why}</div>
    `;

    card.addEventListener("mousemove", (e) => {
      const r = card.getBoundingClientRect();
      const mx = ((e.clientX - r.left) / r.width) * 100;
      const my = ((e.clientY - r.top) / r.height) * 100;
      card.style.setProperty("--mx", `${mx}%`);
      card.style.setProperty("--my", `${my}%`);
    });

    card.querySelector(".openBtn").onclick = (e) => { e.stopPropagation(); openModal(p); };
    card.onclick = () => openModal(p);

    card.querySelector(".pinBtn").onclick = async (e) => {
      e.stopPropagation();
      await toggleFav(p);
    };

    results.appendChild(card);
  });
}

/* ✅ update current mood on backend */
async function setCurrentMood(mood) {
  try {
    await fetch("/api/mood/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mood })
    });
  } catch { }
}

/* ✅ improved empty response UI */
function showNoPlacesMessage() {
  const results = document.getElementById("results");
  results.innerHTML = `
    <div style="opacity:.75; padding:45px 0; text-align:center;">
      ⚠️ No places found.<br>
      <span style="font-size:13px;opacity:.75;">
        Overpass may be slow / rate-limited. Try again in a few seconds.
      </span>
    </div>
  `;
}

/* =========================================================
   ✅ Find places (Fixed: waits for fresh GPS + smart retry)
   ========================================================= */

async function fetchPlacesFromAPI(mood) {
  const r = await fetch("/api/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mood, latitude: userLat, longitude: userLon })
  });

  const data = await r.json();
  return Array.isArray(data) ? data : [];
}

/* ✅ Find places (Improved) */
async function findPlaces() {
  if (loading) return;
  loading = true;

  const mood = document.getElementById("mood").value;
  applyMoodTheme(mood);
  setCurrentMood(mood);

  setFindBtnState(true, "Searching…");
  setSkeleton();

  try {
    console.log("🟡 Before ensureFreshLocation -> ready:", ready, "lat:", userLat, "lon:", userLon);

    const ok = await ensureFreshLocation(10000);

    console.log("🟢 After ensureFreshLocation -> ok:", ok, "ready:", ready, "lat:", userLat, "lon:", userLon, "acc:", lastGeoAcc);

    if (!ok || !isLocationValid()) {
      showNoPlacesMessage();
      return;
    }

    await fetchFavoritesFromDB();
    renderSavedGrouped();

    console.log("➡️ Calling /api/recommend ...");

    const r = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mood, latitude: userLat, longitude: userLon })
    });

    const data = await r.json();
    lastPlaces = Array.isArray(data) ? data : [];

    console.log("✅ /api/recommend returned:", lastPlaces.length, "places");

    if (!lastPlaces.length) {
      showNoPlacesMessage();
      return;
    }

    renderPlacesFiltered();
  } catch (e) {
    console.log("❌ findPlaces error:", e);
    lastPlaces = [];
    showNoPlacesMessage();
  } finally {
    loading = false;
    setFindBtnState(false, "Find nearby places");

    await fetchFavoritesFromDB();
    renderSavedGrouped();
  }
}

/* init */
(async function init() {
  await fetchFavoritesFromDB();
  renderSavedGrouped();
})();

/* refresh status every 30 sec */
setInterval(() => {
  if (lastPlaces.length) renderPlacesFiltered();
}, 30000);

document.getElementById("mood").addEventListener("change", () => {
  const mood = document.getElementById("mood").value;
  applyMoodTheme(mood);
  if (lastPlaces.length) renderPlacesFiltered();
});


/* ===================== PROFILE + REQUESTS ===================== */

const myProfileBtn = document.getElementById("myProfileBtn");
const copyProfileBtn = document.getElementById("copyProfileBtn");

/* ✅ Fix: always set correct username from backend */
async function hydrateMyUsername() {
  try {
    const r = await fetch("/api/profile/me");
    const d = await r.json();

    if (d && d.success && d.username) {
      window.MOODMAP_USERNAME = d.username;
      return d.username;
    }
  } catch { }

  return window.MOODMAP_USERNAME || "";
}

/* ✅ Robust clipboard copy */
async function copyTextSmart(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { }

  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

(async function initProfileButtons() {
  await hydrateMyUsername();

  if (myProfileBtn) {
    myProfileBtn.onclick = async () => {
      const uname = (window.MOODMAP_USERNAME || "").trim();

      if (uname.length > 0) {
        location.href = "/u/" + uname;
      } else {
        alert("Your username is not set. Please logout → signup again with a username.");
      }
    };
  }

  if (copyProfileBtn) {
    copyProfileBtn.onclick = async () => {
      const uname = (window.MOODMAP_USERNAME || "").trim();
      if (!uname) {
        alert("Username not available. Please open My Profile first.");
        return;
      }

      const url = location.origin + "/u/" + uname;
      const ok = await copyTextSmart(url);

      if (ok) {
        const old = copyProfileBtn.innerText;
        copyProfileBtn.innerText = "✅ Copied";
        setTimeout(() => copyProfileBtn.innerText = old || "🔗 Copy Link", 1200);
      } else {
        alert("Copy failed. Link:\n" + url);
      }
    };
  }
})();


/* follow requests modal */
const reqBtn = document.getElementById("reqBtn");
const reqCount = document.getElementById("reqCount");
const reqBackdrop = document.getElementById("reqBackdrop");
const reqModal = document.getElementById("reqModal");
const reqCloseBtn = document.getElementById("reqCloseBtn");
const reqList = document.getElementById("reqList");

function openReqModal() {
  reqBackdrop.classList.add("active");
  reqModal.classList.add("active");
}
function closeReqModal() {
  reqBackdrop.classList.remove("active");
  reqModal.classList.remove("active");
}
if (reqBackdrop) reqBackdrop.addEventListener("click", closeReqModal);
if (reqCloseBtn) reqCloseBtn.onclick = closeReqModal;

async function fetchRequests() {
  try {
    const r = await fetch("/api/follow/requests");
    const data = await r.json();
    const list = Array.isArray(data) ? data : [];
    const n = list.length;

    if (reqBtn && reqCount) {
      if (n > 0) {
        reqBtn.style.display = "inline-flex";
        reqCount.innerText = `(${n})`;
      } else {
        reqBtn.style.display = "none";
        reqCount.innerText = "";
      }
    }

    if (!reqList) return;

    reqList.innerHTML = "";
    if (!n) {
      reqList.innerHTML = `<div style="opacity:.65;font-size:13px;padding:18px;">No pending requests.</div>`;
      return;
    }

    list.forEach(item => {
      const div = document.createElement("div");
      div.className = "reqItem";
      div.innerHTML = `
        <div>
          <div class="reqUser">@${item.username}</div>
          <div class="reqMeta">${item.name || ""}</div>
        </div>
        <div class="reqBtns">
          <button class="smallBtn primary">Accept</button>
          <button class="smallBtn danger">Reject</button>
        </div>
      `;

      const [acceptBtn, rejectBtn] = div.querySelectorAll("button");

      acceptBtn.onclick = async () => {
        acceptBtn.disabled = true;
        await fetch("/api/follow/requests/accept", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ req_id: item.req_id })
        });
        await fetchRequests();
      };

      rejectBtn.onclick = async () => {
        rejectBtn.disabled = true;
        await fetch("/api/follow/requests/reject", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ req_id: item.req_id })
        });
        await fetchRequests();
      };

      reqList.appendChild(div);
    });

  } catch { }
}

if (reqBtn) {
  reqBtn.onclick = async () => {
    await fetchRequests();
    openReqModal();
  };
}

/* poll requests (Render-safe + no spam) */
function canPollRequests() {
  // only poll when page is visible (prevents background spam)
  if (document.visibilityState !== "visible") return false;

  // if button doesn't exist on this page, don't poll
  if (!reqBtn || !reqCount) return false;

  return true;
}

async function pollRequestsSafe() {
  try {
    if (!canPollRequests()) return;
    await fetchRequests();
  } catch { }
}

function startFollowRequestPolling() {
  stopFollowRequestPolling();

  // ✅ immediate 1st fetch
  pollRequestsSafe();

  // ✅ reduce spam (from 12s -> 60s)
  followReqPollTimer = setInterval(pollRequestsSafe, 60000);
}

function stopFollowRequestPolling() {
  if (followReqPollTimer) {
    clearInterval(followReqPollTimer);
    followReqPollTimer = null;
  }
}

/* init requests */
(function initRequests() {
  startFollowRequestPolling();

  // ✅ stop polling when tab hidden; restart when visible
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      startFollowRequestPolling();
    } else {
      stopFollowRequestPolling();
    }
  });

  // ✅ stop polling on unload
  window.addEventListener("beforeunload", stopFollowRequestPolling);
})();



/* ===================== USER SEARCH ===================== */

const userSearch = document.getElementById("userSearch");
const userDrop = document.getElementById("userDrop");
const dropList = document.getElementById("dropList");
const searchWrap = document.getElementById("searchWrap");

let searchTimer = null;
let lastQuery = "";

function openDrop() {
  if (userDrop) userDrop.style.display = "block";
}
function closeDrop() {
  if (userDrop) userDrop.style.display = "none";
}

async function doUserSearch(q) {
  if (!dropList) return;

  if (!q || q.length < 2) {
    dropList.innerHTML = `<div class="emptyDrop">Type at least 2 letters.</div>`;
    return;
  }

  dropList.innerHTML = `<div class="emptyDrop">Searching…</div>`;

  try {
    const r = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
    const data = await r.json();
    const list = Array.isArray(data) ? data : [];

    if (q !== lastQuery) return;

    if (!list.length) {
      dropList.innerHTML = `<div class="emptyDrop">No users found.</div>`;
      return;
    }

    dropList.innerHTML = "";
    list.forEach(u => {
      const item = document.createElement("div");
      item.className = "dropItem";

      const pfp = (u.profile_pic || "").trim();
      const letter = (u.name || "U").slice(0, 1).toUpperCase();

      item.innerHTML = `
        <div class="dropLeft">
          <div class="avatar">
            ${pfp ? `<img src="${pfp}" style="width:100%;height:100%;object-fit:cover;border-radius:14px;" />` : letter}
          </div>
          <div style="min-width:0;">
            <div class="dropName">${u.name}</div>
            <div class="dropUser">@${u.username}</div>
          </div>
        </div>
        <div class="lockBadge">${u.is_private ? " Private" : " Public"}</div>
      `;
      item.onclick = () => {
        location.href = "/u/" + u.username;
      };
      dropList.appendChild(item);
    });

  } catch {
    dropList.innerHTML = `<div class="emptyDrop">Error searching users.</div>`;
  }
}

if (userSearch) {
  userSearch.addEventListener("input", () => {
    const q = (userSearch.value || "").trim().toLowerCase();
    lastQuery = q;
    openDrop();

    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      doUserSearch(q);
    }, 250);
  });

  userSearch.addEventListener("focus", () => {
    openDrop();
    doUserSearch((userSearch.value || "").trim().toLowerCase());
  });
}

/* close dropdown when clicking outside */
document.addEventListener("click", (e) => {
  if (searchWrap && !searchWrap.contains(e.target)) {
    closeDrop();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDrop();
  }
});
