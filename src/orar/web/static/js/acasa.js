// Selectorul in trepte de pe pagina principala (templates/index.html): taburile Grupe / Sali
// si listele nivel -> domeniu -> an -> grupa, umplute din JSON-ul `#alege-date`.
(() => {
  const radacina = document.getElementById("alege");
  const arbore = JSON.parse(document.getElementById("alege-date").textContent);
  // orarul ales (alt semestru, o versiune veche) se pastreaza in adresa paginii deschise
  const qv = radacina.dataset.qv;
  radacina.hidden = false;

  // taburile: Grupe / Sali
  const taburi = [...radacina.querySelectorAll('[role="tab"]')];
  const arata = (tab) => taburi.forEach((t) => {
    const ales = t === tab;
    t.setAttribute("aria-selected", ales);
    t.tabIndex = ales ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !ales;
  });
  taburi.forEach((t, i) => {
    t.addEventListener("click", () => arata(t));
    t.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const urm = taburi[(i + (e.key === "ArrowRight" ? 1 : taburi.length - 1)) % taburi.length];
      arata(urm); urm.focus();
    });
  });

  // grupe: fiecare treapta o deschide pe urmatoarea
  const [nivel, domeniu, an, grupa] = ["nivel", "domeniu", "an", "grupa"].map((n) => document.getElementById("alege-" + n));
  const panou = document.getElementById("panou-grupe");
  const buton = panou.querySelector("button");
  const umple = (sel, optiuni, gol) => {
    sel.replaceChildren(new Option(gol, ""), ...optiuni.map(([text, val]) => new Option(text, val)));
    sel.disabled = optiuni.length === 0;
  };
  const ales = () => {
    const n = arbore[nivel.value], d = n && n.domenii[domeniu.value], a = d && d.ani[an.value];
    return { n, d, a };
  };
  const tinta = () => { const { a } = ales(); return a ? (grupa.value || a.slug) : ""; };
  const actualizeaza = () => { buton.disabled = !tinta(); };

  umple(nivel, arbore.map((n, i) => [n.nume, i]), "alege nivelul");
  nivel.addEventListener("change", () => {
    const { n } = ales();
    umple(domeniu, n ? n.domenii.map((d, i) => [d.nume, i]) : [], "alege domeniul");
    umple(an, [], "alege anul"); umple(grupa, [], "alege grupa"); actualizeaza();
  });
  domeniu.addEventListener("change", () => {
    const { d } = ales();
    umple(an, d ? d.ani.map((a, i) => ["Anul " + a.an, i]) : [], "alege anul");
    umple(grupa, [], "alege grupa"); actualizeaza();
  });
  an.addEventListener("change", () => {
    const { a } = ales();
    umple(grupa, a ? a.grupe.map((g) => [g.nume, g.slug]) : [], "tot anul");
    actualizeaza();
  });
  grupa.addEventListener("change", actualizeaza);
  panou.addEventListener("submit", (e) => {
    e.preventDefault();
    if (tinta()) location.href = "/grupa/" + tinta() + qv;
  });

  // sali
  const sala = document.getElementById("alege-sala");
  const panouSali = document.getElementById("panou-sali");
  sala.addEventListener("change", () => { panouSali.querySelector("button").disabled = !sala.value; });
  panouSali.addEventListener("submit", (e) => {
    e.preventDefault();
    if (sala.value) location.href = "/sala/" + sala.value + qv;
  });
})();
