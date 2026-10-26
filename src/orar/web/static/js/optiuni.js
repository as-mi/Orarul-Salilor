// Dropdownurile din randul de filtre: "Opționale", "Facultative", "Limbi străine",
// "Tutoriate", "Evenimente personale" și "Importă activități". Toate se comportă la fel:
// când se deschide unul, celelalte se închid; panoul se închide la click în afara lui și,
// lângă marginea dreaptă, se deschide spre stânga ca să rămână pe ecran.
const dropdownuri = [...document.querySelectorAll("details.optiuni")];

for (const d of dropdownuri) {
  d.addEventListener("toggle", () => {
    if (!d.open) return;
    // Accordion: un singur panou deschis.
    for (const alt of dropdownuri) {
      if (alt !== d) alt.open = false;
    }
    const panou = d.querySelector(".panou-optiuni");
    if (panou) {
      panou.classList.remove("spre-stanga");
      if (panou.getBoundingClientRect().right > window.innerWidth - 8) {
        panou.classList.add("spre-stanga");
      }
    }
  });
  // Panoul se inchide la click in afara lui.
  document.addEventListener("click", (e) => {
    if (d.open && !d.contains(e.target)) d.open = false;
  });
}

// Dropdownurile de opționale, facultative și limbi au cele trei niveluri de bife
// ("Selectează tot" > materie > activitate), comandate de JS. Nivelurile de sus
// bifeaza/debifeaza tot ce e sub ele si arata starea partiala (-). Activitatile unei
// materii se strang, afara de cele alese partial. Fara JS, bifele activitatilor merg
// oricum si toate sunt la vedere.
for (const d of dropdownuri) {
  const toate = d.querySelector("[data-toate]");
  if (!toate) continue; // celelalte dropdownuri nu au acest mecanism

  const bife = [...d.querySelectorAll('input[name="vizibile"]')];
  const materii = [...d.querySelectorAll("[data-materie]")].map((el) => ({
    el,
    cap: el.querySelector("[data-grup]"),
    copii: [...el.querySelectorAll('input[name="vizibile"]')],
    numar: el.querySelector(".extinde .numar"),
    buton: el.querySelector(".extinde"),
  }));
  const stare = (cap, lista) => {
    const n = lista.filter((b) => b.checked).length;
    cap.checked = n === lista.length;
    cap.indeterminate = n > 0 && n < lista.length;
    return n;
  };
  const sincron = () => {
    for (const m of materii) m.numar.textContent = `${stare(m.cap, m.copii)}/${m.copii.length}`;
    stare(toate, bife);
  };
  const extinde = (m, deschis) => {
    m.el.classList.toggle("strans", !deschis);
    m.buton.setAttribute("aria-expanded", String(deschis));
  };
  toate.addEventListener("change", () => { bife.forEach((b) => (b.checked = toate.checked)); sincron(); });
  for (const m of materii) {
    m.cap.addEventListener("change", () => { m.copii.forEach((b) => (b.checked = m.cap.checked)); sincron(); });
    m.buton.addEventListener("click", () => extinde(m, m.el.classList.contains("strans")));
    extinde(m, m.el.dataset.partiala === "1");
  }
  bife.forEach((b) => b.addEventListener("change", sincron));
  sincron();
}
