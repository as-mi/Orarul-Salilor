// Filtrele orarului de statistici (templates/_statistici_panou.html).
(() => {
  // Statisticile pot aparea si pe pagina intreaga, si in panoul de admin (incarcat prin htmx):
  // ascultatorii se inregistreaza o singura data, iar grupurile de bife se pregatesc dupa fiecare swap.
  if (window.__statisticiJs) return;
  window.__statisticiJs = true;

  const pregateste = () => {
    const filtre = document.getElementById("filtre");
    if (!filtre) return;
    for (const grup of filtre.querySelectorAll(".bife-rand")) {
      const bifele = [...grup.querySelectorAll('input[type="checkbox"]')];
      if (bifele.length < 2 || grup.querySelector(".toate")) continue;
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
  };

  // Valorile implicite nu au ce cauta in adresa: o tin scurta si usor de salvat. Captura, ca
  // sa ruleze inaintea trimiterii (inclusiv a celei facute de htmx din panoul de admin).
  document.addEventListener("submit", (e) => {
    const filtre = e.target.closest ? e.target.closest("#filtre") : null;
    if (!filtre) return;
    const implicite = { ora_de_la: filtre.dataset.oraMin, ora_pana_la: filtre.dataset.oraMax, marime_semigrupa: "15", marime_grupa: "30", prag_plin: "100", metrica: "persoane", saptamana: "", profesor: "", materie: "" };
    for (const camp of filtre.elements)
      if (camp.name in implicite && camp.value === implicite[camp.name]) camp.disabled = true;
  }, true);

  document.addEventListener("htmx:afterSwap", pregateste);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pregateste);
  else pregateste();
})();
