#!/usr/bin/env python

from setuptools import setup, find_packages
import libreborme
import sys

version = '20180531.dev0'


def get_install_requires():
    """
    parse requirements.txt, ignore links, exclude comments
    """
    requirements = []

    for line in open('requirements/base.txt').readlines():
        # skip to next iteration if comment or empty line
        if line.startswith('#') or line == '' or line.startswith('http') or line.startswith('git'):
            continue

        # add line to requirements
        requirements.append(line)

    return requirements


if sys.version_info[0] == 3:
    long_description = open('README.md', encoding='utf-8').read()
else:
    long_description = open('README.md').read()

setup(
    name='libreborme',
    version=version,
    description=libreborme.__doc__,
    long_description=long_description,
    author='Pablo Castellano',
    author_email='pablo@anche.no',
    license='GPLv3',
    url='https://github.com/PabloCastellano/libreborme',
    download_url='https://github.com/PabloCastellano/libreborme/releases',
    packages=find_packages(exclude=['docs', 'docs.*']),
    include_package_data=True,
    zip_safe=False,
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Framework :: Django',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3',
        'Topic :: Internet :: WWW/HTTP',
    ],
    install_requires=get_install_requires(),
    scripts=['libreborme/bin/libreborme'],
    test_suite='runtests.runtests',
)
