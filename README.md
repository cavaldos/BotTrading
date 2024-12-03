# Bot_Trade


1. Create environment
```bash
python -m venv venv
```
2. Activate environment
```bash
source venv/bin/activate # Linux
```
3. update pip
```bash
pip install --upgrade pip
```
4. Install requirements
```bash
pip install -r requirements.txt
```

5. Export requirements
```bash
pip freeze > requirements.txt
```
6. Docker
```bash
docker build -t my-python-app .
docker run -d my-python-app
```