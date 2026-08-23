# Compress & Accélère

Application pour **compresser** et **accélérer** des vidéos MP4 : version Windows et version web (navigateur / GitHub Pages).

## Version web (GitHub Pages)

Tout se passe dans le navigateur. Aucun serveur n’est nécessaire.

Dépôt : https://github.com/mourahi/accelere_compress_video

1. Dans le dépôt : **Settings → Pages**.
2. Source : **Deploy from a branch**.
3. Branche : `master`, dossier : **/docs**.
4. Enregistrez. L’adresse sera :
   https://mourahi.github.io/accelere_compress_video/

Utilisation :

1. Ouvrez la page, attendez « Prêt ».
2. **Ajouter** une vidéo.
3. Indiquez la taille en Mo, la vitesse, et éventuellement **Supprimer l'audio**.
4. Optionnel : **Début ici** / **Fin ici** pour n’exporter qu’une plage (ça accélère l’encodage). **Image** enregistre l’image affichée.
5. **Lancer**. Le fichier MP4 se télécharge ensuite.
6. **Télécharger** relance le téléchargement du dernier export.

Le premier chargement télécharge FFmpeg (~25 Mo), puis le navigateur le garde en cache.

Pour tester en local avant de publier :

```
python -m http.server 8080 --directory docs
```

Puis ouvrez `http://localhost:8080`. Ne pas ouvrir `index.html` en double-cliquant (`file://`) : le navigateur bloquerait FFmpeg.

### Navigateurs compatibles

| Navigateur | Encodage accéléré (WebCodecs) | Secours FFmpeg Wasm |
| --- | --- | --- |
| Chrome / Edge / Opera (récents) | Oui | Oui |
| Firefox récent | Souvent oui (H.264 selon le système) | Oui |
| Safari 17+ (macOS / iOS) | Oui si H.264 est encodable | Oui |
| Internet Explorer | Non | Non |

La page doit être ouverte en **HTTPS** (GitHub Pages) ou en **localhost**. Sur téléphone, restez sur l’onglet si la mémoire est limitée.

## Version Windows

Double-cliquez sur `lancer.bat`.

Au premier lancement, l'application installe les dépendances Python et télécharge FFmpeg.

En plus de la compression et de la vitesse, la version Windows permet d’**éditer** :

- couper le début / la fin, ou garder plusieurs extraits
- joindre toutes les vidéos en un seul fichier
- rotation, miroir, recadrage (16:9, 9:16, 1:1, 4:3)
- volume, fondus, texte overlay
- luminosité / contraste / saturation
- inverser la vidéo
- remplacer l’audio par un MP3
- extraire l’image affichée (JPEG / PNG)
- supprimer le fichier d’origine après un export réussi, via un bouton dédié

## Prérequis Windows

- Windows 10 ou 11
- [Python 3](https://www.python.org/downloads/) avec l'option **Add python.exe to PATH**
