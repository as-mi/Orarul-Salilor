// Filtrele orarului de statistici (templates/admin_statistici.html).
const filtre = document.getElementById("filtre");

// Fiecare grup de bife primeste un "toate", care il bifeaza sau il debifeaza dintr-un click
// si tine pasul cu bifele lui (liniuta cand sunt bifate doar unele).
for (const grup of document.querySelectorAll("#filtre .bife-rand")) {
  const bifele = [...grup.querySelectorAll('input[type="checkbox"]')];
  if (bifele.length < 2) continue;
  const eticheta = document.createElement("label");
  eticheta.className = "bifa toate";
  eticheta.innerHTML = '<input type="checkbox"><span><strong>toate</strong></span>';
  const toate = eticheta.firstElementChild;
  grup.prepend(eticheta);
  const tine_pasul = () => {
    const bifate = bifele.filter((b) => b.checked).length;
    toate.checked = bifate === bifele.length;
    toate.indeterminate = bifate > 0 && !toate.checked;
  };
  toate.addEventListener("change", () => { bifele.forEach((b) => { b.checked = toate.checked; }); tine_pasul(); });
  bifele.forEach((b) => b.addEventListener("change", tine_pasul));
  tine_pasul();
}

// valorile implicite nu au ce cauta in adresa: o tin scurta si usor de salvat
filtre.addEventListener("submit", (e) => {
  const implicite = { ora_de_la: filtre.dataset.oraMin, ora_pana_la: filtre.dataset.oraMax, marime_semigrupa: "15", marime_grupa: "30", prag_plin: "100", metrica: "persoane", saptamana: "", profesor: "", materie: "" };
  for (const camp of e.target.elements)
    if (camp.name in implicite && camp.value === implicite[camp.name]) camp.disabled = true;
});
