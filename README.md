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
4. **Lancer**. Le fichier MP4 se télécharge ensuite.
5. **Télécharger** relance le téléchargement du dernier export.

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

## Prérequis Windows

- Windows 10 ou 11
- [Python 3](https://www.python.org/downloads/) avec l'option **Add python.exe to PATH**
