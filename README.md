# Tendances de la semaine

Chaque lundi, un petit robot choisit une trentaine de **recettes de saison appréciées** sur des sites de cuisine français et quelques blogs.

- Il lit le plan public des sites (ou le flux RSS des blogs), en respectant leur fichier `robots.txt` et en attendant 2 secondes entre deux pages.
- Il garde les recettes dont tous les fruits et légumes sont de saison ce mois-ci, puis les classe selon leur note et leur nombre d'avis.
- Il ne recopie aucune recette : le fichier publié (`docs/tendances.json`) contient seulement le titre, le site, le lien, la note et les produits de saison. Chaque recette reste sur son site d'origine.

L'application de planning de repas affiche cette sélection. L'utilisateur ouvre la page d'origine, ou importe la recette s'il le souhaite.

Un site ou un blog qui ne souhaite pas apparaître peut ouvrir une « issue » sur ce dépôt : il sera retiré.
