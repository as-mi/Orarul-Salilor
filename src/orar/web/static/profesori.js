// Alegerea profesorilor in formularele de editare: vezi templates/_profesori.html.
(() => {
  const ALT = document.currentScript.dataset.alt;
  const sablon = document.getElementById("optiuni-profesori");

  // Un dropdown "lenes" are doar valoarea lui; restul listei vine la prima folosire.
  const umple = (select) => {
    if (!select.hasAttribute("data-lenes")) return;
    select.removeAttribute("data-lenes");
    const avute = new Set([...select.options].map((o) => o.value));
    for (const o of sablon.content.querySelectorAll("option")) {
      if (avute.has(o.value)) {
        // numele citit e chiar unul din lista: scapa de eticheta "citit:"
        const vechi = [...select.options].find((x) => x.value === o.value);
        if (vechi.value) vechi.textContent = o.textContent;
      } else {
        select.append(o.cloneNode(true));
      }
    }
  };

  const sincron = (rand) => {
    const select = rand.querySelector("select");
    const camp = rand.querySelector('input[type="text"]');
    const alt = select.value === ALT;
    camp.hidden = camp.disabled = !alt;
    if (alt) camp.focus();
  };

  const rand_nou = (grup, nume = "") => {
    const rand = grup.querySelector(".profesor-rand").cloneNode(true);
    const select = rand.querySelector("select");
    umple(select);
    // "citit: ..." e al randului copiat, nu al celui nou
    for (const o of [...select.options]) if (o.textContent.startsWith("citit: ")) o.remove();
    select.value = nume;
    rand.querySelector('input[type="text"]').value = "";
    grup.querySelector("[data-adauga-profesor]").before(rand);
    sincron(rand);
    return rand;
  };

  document.addEventListener("focusin", (e) => { if (e.target.matches("select[data-lenes]")) umple(e.target); });
  document.addEventListener("mousedown", (e) => { if (e.target.matches("select[data-lenes]")) umple(e.target); });
  document.addEventListener("change", (e) => {
    const rand = e.target.closest(".profesor-rand");
    if (rand && e.target.matches("select")) sincron(rand);
  });

  document.addEventListener("click", (e) => {
    const adauga = e.target.closest("[data-adauga-profesor]");
    if (adauga) {
      rand_nou(adauga.closest(".profesori")).querySelector("select").focus();
      return;
    }
    const scoate = e.target.closest("[data-scoate-profesor]");
    if (scoate) {
      const rand = scoate.closest(".profesor-rand");
      if (rand.parentElement.querySelectorAll(".profesor-rand").length > 1) {
        rand.remove();
      } else {
        const select = rand.querySelector("select");
        umple(select);
        select.value = "";
        sincron(rand);
      }
      return;
    }
    // "foloseste": pune exact profesorii propusi de orarul profesorilor
    const foloseste = e.target.closest("[data-foloseste]");
    if (foloseste) {
      const grup = foloseste.closest("form").querySelector(".profesori");
      const nume = foloseste.dataset.foloseste.split("|");
      const randuri = [...grup.querySelectorAll(".profesor-rand")];
      randuri.slice(1).forEach((r) => r.remove());
      const primul = randuri[0].querySelector("select");
      umple(primul);
      primul.value = nume[0];
      sincron(randuri[0]);
      nume.slice(1).forEach((n) => rand_nou(grup, n));
    }
  });
})();
