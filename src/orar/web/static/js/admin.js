// Panoul de administrare: bara din stanga alege ce panou se vede in dreapta. Fara
// JavaScript, panourile ramin toate vizibile, iar linkurile din bara duc la ancorele lor.
// Pe telefon, bara se strange dupa butonul cu trei linii si se inchide la clic pe fundal,
// pe o legatura sau cu Escape.
//
// Panourile grele (statistici, coada de verificare, anul universitar) se incarca la prima
// deschidere prin htmx, de pe adresa din `data-incarca`; linkurile si formularele din ele
// care duc la aceeasi sectiune reincarca doar panoul, nu toata pagina.
(() => {
  const panouri = [...document.querySelectorAll(".admin-panou[data-sectiune]")];
  if (!panouri.length) return;

  // De acum bara poate fi ascunsa; fara clasa, ramane deschisa si fara buton.
  document.documentElement.classList.add("admin-cu-js");

  const cadru = document.querySelector(".admin-cadru");
  const bara = document.getElementById("admin-bara");
  const butonBara = document.getElementById("admin-bara-buton");
  const fundal = document.getElementById("admin-fundal");

  if (cadru && bara && butonBara && fundal) {
    const inchideBara = bara.querySelector(".admin-bara-inchide");
    const puneBara = (deschisa, intoarceFocus = false) => {
      cadru.classList.toggle("are-bara", deschisa);
      butonBara.setAttribute("aria-expanded", String(deschisa));
      fundal.hidden = !deschisa;
      document.body.classList.toggle("fara-derulare", deschisa);
      if (deschisa) inchideBara?.focus();
      else if (intoarceFocus) butonBara.focus();
    };

    butonBara.addEventListener("click", () => puneBara(!cadru.classList.contains("are-bara")));
    inchideBara.addEventListener("click", () => puneBara(false, true));
    fundal.addEventListener("click", () => puneBara(false, true));
    bara.addEventListener("click", (e) => {
      if (e.target.closest("a")) puneBara(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && cadru.classList.contains("are-bara")) puneBara(false, true);
    });

    // Daca fereastra se largeste cit timp bara e deschisa, revine la asezarea de desktop.
    const lat = window.matchMedia("(min-width: 901px)");
    const laSchimbare = (e) => { if (e.matches) puneBara(false); };
    if (lat.addEventListener) lat.addEventListener("change", laSchimbare);
    else lat.addListener(laSchimbare);
  }

  // Panoul implicit: ce e de facut acum si actiunile rapide.
  const implicit = "panou";
  // Redirectarile existente trimit la ancore din interiorul panourilor.
  const aliasuri = { orare: "orar" };

  const incarca = (panou) => {
    if (panou.dataset.incarcat || !panou.dataset.incarca) return;
    panou.dataset.incarcat = "1";
    htmx.ajax("GET", panou.dataset.incarca, {
      target: panou,
      swap: "innerHTML",
      select: panou.dataset.select,
    });
  };

  const arata = (cheie) => {
    const cerut = aliasuri[cheie] || cheie;
    const ales = panouri.some((p) => p.dataset.sectiune === cerut) ? cerut : implicit;
    for (const panou of panouri) {
      panou.hidden = panou.dataset.sectiune !== ales;
      if (panou.dataset.sectiune === ales) incarca(panou);
    }
    for (const legatura of document.querySelectorAll(".admin-bara [data-panou]")) {
      const activ = legatura.dataset.panou === ales;
      legatura.classList.toggle("activ", activ);
      if (activ) legatura.setAttribute("aria-current", "page");
      else legatura.removeAttribute("aria-current");
    }
  };

  // Orice legatura cu `data-panou` (bara sau actiunile rapide) schimba panoul.
  document.addEventListener("click", (e) => {
    const legatura = e.target.closest("[data-panou]");
    if (!legatura || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const cheie = legatura.dataset.panou;
    arata(cheie);
    history.replaceState(null, "", "#" + cheie);
  });

  // In panourile incarcate pe loc, linkurile care duc la aceeasi sectiune reincarca doar
  // panoul (htmx), ca navigarea sa nu paraseasca panoul de administrare.
  document.addEventListener("click", (e) => {
    const legatura = e.target.closest("a[href]");
    const panou = legatura && legatura.closest(".admin-panou[data-select]");
    if (!panou || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (legatura.hasAttribute("hx-get") || legatura.hasAttribute("hx-post")) return;
    if (legatura.closest("[data-extern]")) return;
    const prefix = new URL(panou.dataset.incarca, location.href).pathname;
    const tinta = new URL(legatura.href, location.href);
    if (tinta.pathname !== prefix && !tinta.pathname.startsWith(prefix + "/")) return;
    e.preventDefault();
    const tintaPanou = panou.firstElementChild;
    if (!tintaPanou) return;
    htmx.ajax("GET", tinta.pathname + tinta.search, {
      target: tintaPanou,
      swap: "outerHTML",
      select: panou.dataset.select,
    });
  });

  // La fel pentru formularele din aceleasi panouri (salvarea/stergerea unui filtru, a unei
  // perioade etc.); formularele cu hx-post deja se rezolva singure.
  document.addEventListener("submit", (e) => {
    const form = e.target && e.target.closest ? e.target.closest("form") : e.target;
    const panou = form && form.closest(".admin-panou[data-select]");
    if (!panou) return;
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get")) return;
    if (form.closest("[data-extern]")) return;
    const prefix = new URL(panou.dataset.incarca, location.href).pathname;
    const actiune = form.getAttribute("action") || location.pathname;
    const tinta = new URL(actiune, location.href);
    if (tinta.pathname !== prefix && !tinta.pathname.startsWith(prefix + "/")) return;
    e.preventDefault();
    const tintaPanou = panou.firstElementChild;
    if (!tintaPanou) return;
    htmx.ajax((form.method || "post").toLowerCase(), tinta.pathname + tinta.search, {
      source: form,
      target: tintaPanou,
      swap: "outerHTML",
      select: panou.dataset.select,
    });
  });

  // Cautarea in lista de profesori, ca la utilizatori.
  document.addEventListener("input", (e) => {
    if (e.target.id !== "cauta-profesor") return;
    const q = e.target.value.trim().toLowerCase();
    for (const li of document.querySelectorAll("#lista-profesori-admin li"))
      li.hidden = q && !li.dataset.cauta.includes(q);
  });

  window.addEventListener("hashchange", () => arata(location.hash.slice(1)));
  arata(location.hash.slice(1));
})();
