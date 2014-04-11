test:
	nosetests

coverage:
	nosetests --with-coverage --cover-package=orwell --cover-tests
