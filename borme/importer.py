from .models import Company, Borme, Anuncio, Person, BormeLog
from .utils import slug2

from django.db import connection, transaction
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone

from django.contrib.postgres.search import SearchVector

import bormeparser

from bormeparser.borme import BormeXML
from bormeparser.exceptions import BormeDoesntExistException
from bormeparser.regex import (
        is_company,
        is_acto_cargo_entrante,
        regex_empresa_tipo)
from bormeparser.utils import FIRST_BORME

import datetime
import logging
import time
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ch = logging.StreamHandler()
logger.addHandler(ch)
logger.setLevel(logging.INFO)


def _import1(borme):
    """
    borme: bormeparser.Borme
    """
    logger.info("\nBORME CVE: {} ({}, {}, [{}-{}])"
                .format(borme.cve, borme.date, borme.provincia,
                        borme.anuncios_rango[0], borme.anuncios_rango[1]))
    results = {
        'created_anuncios': 0,
        'created_bormes': 0,
        'created_companies': 0,
        'created_persons': 0,
        'total_anuncios': 0,
        'total_bormes': 0,
        'total_companies': 0,
        'total_persons': 0,
        'errors': 0
    }

    try:
        nuevo_borme = Borme.objects.get(cve=borme.cve)
    except Borme.DoesNotExist:
        nuevo_borme = Borme(cve=borme.cve, date=borme.date, url=borme.url,
                            from_reg=borme.anuncios_rango[0],
                            until_reg=borme.anuncios_rango[1],
                            province=borme.provincia.name,
                            section=borme.seccion)
        # year, type, from_page, until_page, pages
        # num?, filename?
        logger.debug('Creando borme %s' % borme.cve)
        nuevo_borme.save()
        results['created_bormes'] += 1

    try:
        borme_log = BormeLog.objects.get(borme=nuevo_borme)
    except BormeLog.DoesNotExist:
        borme_log = BormeLog(borme=nuevo_borme, path=borme.filename)

    if borme_log.parsed:
        logger.warn('%s ya ha sido analizado.' % borme.cve)
        return results

    borme_log.save()  # date_updated

    borme_embed = {'cve': nuevo_borme.cve, 'url': nuevo_borme.url}
    for n, anuncio in enumerate(borme.get_anuncios(), 1):
        try:
            logger.debug('%d: Importando anuncio: %s' % (n, anuncio))
            results['total_companies'] += 1
            empresa, tipo = regex_empresa_tipo(anuncio.empresa)
            if tipo == '':
                logger.warn("[{}]: Tipo de empresa no detectado: {}"
                            .format(borme.cve, empresa))
            slug_c = slugify(empresa)
            try:
                company = Company.objects.get(slug=slug_c)
                if company.name != empresa:
                    logger.warn("[{}] WARNING: Empresa similar. Mismo slug: {}"
                                .format(borme.cve, slug_c))
                    logger.warn("[{cve}] {0}\n"
                                "[{cve}] {1}\n"
                                .format(company.name, empresa, cve=borme.cve))
                    results['errors'] += 1
            except Company.DoesNotExist:
                company = Company(name=empresa, type=tipo)
                logger.debug('Creando empresa %s %s' % (empresa, tipo))
                results['created_companies'] += 1

            company.add_in_bormes(borme_embed)

            try:
                nuevo_anuncio = Anuncio.objects.get(id_anuncio=anuncio.id,
                                                    year=borme.date.year)
            except Anuncio.DoesNotExist:
                nuevo_anuncio = Anuncio(
                                id_anuncio=anuncio.id,
                                year=borme.date.year,
                                borme=nuevo_borme,
                                datos_registrales=anuncio.datos_registrales)
                logger.debug("Creando anuncio {}: {} {}"
                             .format(anuncio.id, empresa, tipo))
                results['created_anuncios'] += 1

            for acto in anuncio.get_borme_actos():
                logger.debug(acto.name)
                logger.debug(acto.value)
                if isinstance(acto, bormeparser.borme.BormeActoCargo):
                    lista_cargos = []
                    for nombre_cargo, nombres in acto.cargos.items():
                        logger.debug(
                                "{} {} {}"
                                .format(nombre_cargo, nombres, len(nombres)))
                        for nombre in nombres:
                            logger.debug('  %s' % nombre)
                            if is_company(nombre):
                                results['total_companies'] += 1
                                empresa, tipo = regex_empresa_tipo(nombre)
                                slug_c = slugify(empresa)
                                try:
                                    c = Company.objects.get(slug=slug_c)
                                    if c.name != empresa:
                                        logger.warn('[%s] WARNING: Empresa similar 2. Mismo slug: %s' % (borme.cve, slug_c))
                                        logger.warn('[%s] %s\n[%s] %s %s\n' % (borme.cve, c.name, borme.cve, empresa, tipo))
                                        results['errors'] += 1
                                except Company.DoesNotExist:
                                    c = Company(name=empresa, type=tipo)
                                    logger.debug("Creando empresa: {} {}"
                                                 .format(empresa, tipo))
                                    results['created_companies'] += 1

                                c.anuncios.append(anuncio.id)
                                c.add_in_bormes(borme_embed)

                                cargo = {
                                    'title': nombre_cargo,
                                    'name': c.fullname,
                                    'type': 'company'
                                }
                                if is_acto_cargo_entrante(acto.name):
                                    cargo['date_from'] = borme.date.isoformat()
                                    cargo_embed = {
                                        'title': nombre_cargo,
                                        'name': company.fullname,
                                        'date_from': borme.date.isoformat(),
                                        'type': 'company'
                                    }
                                    c.update_cargos_entrantes([cargo_embed])
                                else:
                                    cargo['date_to'] = borme.date.isoformat()
                                    cargo_embed = {
                                        'title': nombre_cargo,
                                        'name': company.fullname,
                                        'date_to': borme.date.isoformat(),
                                        'type': 'company'
                                    }
                                    c.update_cargos_salientes([cargo_embed])
                                c.date_updated = borme.date
                                c.save()
                            else:
                                results['total_persons'] += 1
                                slug_p = slugify(nombre)
                                try:
                                    p = Person.objects.get(slug=slug_p)
                                    if p.name != nombre:
                                        logger.warn(
                                            "[{}] WARNING: Persona similar. "
                                            "Mismo slug: {}"
                                            .format(borme.cve, slug_p))
                                        logger.warn(
                                            "[{cve}] {0}\n"
                                            "[{cve}] {1}\n"
                                            .format(p.name, nombre,
                                                    cve=borme.cve))
                                        results['errors'] += 1
                                except Person.DoesNotExist:
                                    p = Person(name=nombre)
                                    logger.debug(
                                            "Creando persona: {}"
                                            .format(nombre))
                                    results['created_persons'] += 1

                                p.add_in_companies(company.fullname)
                                p.add_in_bormes(borme_embed)

                                cargo = {
                                    'title': nombre_cargo,
                                    'name': p.name,
                                    'type': 'person'
                                }

                                if is_acto_cargo_entrante(acto.name):
                                    cargo['date_from'] = borme.date.isoformat()
                                    cargo_embed = {
                                        'title': nombre_cargo,
                                        'name': company.fullname,
                                        'date_from': borme.date.isoformat()
                                    }
                                    p.update_cargos_entrantes([cargo_embed])
                                else:
                                    cargo['date_to'] = borme.date.isoformat()
                                    cargo_embed = {
                                        'title': nombre_cargo,
                                        'name': company.fullname,
                                        'date_to': borme.date.isoformat()
                                    }
                                    p.update_cargos_salientes([cargo_embed])

                                p.date_updated = borme.date
                                p.save()
                            lista_cargos.append(cargo)

                    nuevo_anuncio.actos[acto.name] = lista_cargos

                    if is_acto_cargo_entrante(acto.name):
                        company.update_cargos_entrantes(lista_cargos)
                    else:
                        company.update_cargos_salientes(lista_cargos)
                else:
                    # not bormeparser.borme.BormeActoCargo
                    nuevo_anuncio.actos[acto.name] = acto.value

                    if acto.name == 'Extinción':
                        extinguir_sociedad(company, borme.date)

            company.anuncios.append(anuncio.id)  # TODO: year
            company.date_updated = borme.date
            company.save()
            nuevo_anuncio.company = company
            nuevo_anuncio.save()
            nuevo_borme.anuncios.append(anuncio.id)  # TODO: year

        except Exception as e:
            logger.error("[{}] ERROR importing anuncio {}"
                         .format(borme.cve, anuncio.id))
            logger.error("[X] {classname}: {exception}"
                         .format(classname=e.__class__.__name__,
                                 exception=e))
            results['errors'] += 1

    nuevo_borme.save()

    borme_log.errors = results['errors']
    borme_log.parsed = True  # FIXME: Si hay ValidationError, parsed = False
    borme_log.date_parsed = timezone.now()
    borme_log.save()
    return results


def extinguir_sociedad(company, date):
    """
    Se llama a esta función cuando una sociedad se extingue.
    Todos los cargos vigentes pasan a la lista de cargos cesados (historial).
    Modifica los modelos Company y Person.
    company: Company object
    date: datetime.date object
    """
    company.is_active = False
    company.date_extinction = date
    company.date_updated = date

    for cargo in company.cargos_actuales_c:
        cargo['date_to'] = date.isoformat()
        company.cargos_historial_c.append(cargo)
        c_cesada = Company.objects.get(slug=slug2(cargo['name']))
        c_cesada._cesar_cargo(company.fullname, date.isoformat())
        c_cesada.save()

    for cargo in company.cargos_actuales_p:
        cargo['date_to'] = date.isoformat()
        company.cargos_historial_p.append(cargo)
        p_cesada = Person.objects.get(slug=slug2(cargo['name']))
        p_cesada._cesar_cargo(company.fullname, date.isoformat())
        p_cesada.save()

    company.cargos_actuales_c = []
    company.cargos_actuales_p = []
    company.save()


def get_borme_xml_filepath(date):
    year = str(date.year)
    month = '%02d' % date.month
    day = '%02d' % date.day
    filename = 'BORME-S-%s%s%s.xml' % (year, month, day)
    return os.path.join(settings.BORME_XML_ROOT, year, month, filename)


def get_borme_pdf_path(date):
    year = '%02d' % date.year
    month = '%02d' % date.month
    day = '%02d' % date.day
    return os.path.join(settings.BORME_PDF_ROOT, year, month, day)


def get_borme_json_path(date):
    year = '%02d' % date.year
    month = '%02d' % date.month
    day = '%02d' % date.day
    return os.path.join(settings.BORME_JSON_ROOT, year, month, day)


def update_previous_xml(date):
    """
    Dada una fecha, comprueba si el XML anterior es definitivo y si no lo es
    lo descarga de nuevo
    """
    xml_path = get_borme_xml_filepath(date)
    bxml = BormeXML.from_file(xml_path)

    try:
        prev_xml_path = get_borme_xml_filepath(bxml.prev_borme)
        prev_bxml = BormeXML.from_file(prev_xml_path)
        if prev_bxml.is_final:
            return False

        os.unlink(prev_xml_path)
    except OSError:
        pass
    finally:
        prev_bxml = BormeXML.from_date(bxml.prev_borme)
        prev_bxml.save_to_file(prev_xml_path)

    return True


def files_exist(files):
    return all([os.path.exists(f) for f in files])


def import_borme_download(date_from, date_to, seccion=bormeparser.SECCION.A,
                          local_only=False, no_missing=False):
    """
    date_from: "2015-01-30", "init"
    date_to: "2015-01-30", "today"
    """
    if date_from == 'init':
        date_from = FIRST_BORME[2009]
    else:
        date = tuple(map(int, date_from.split('-')))  # TODO: exception
        date_from = datetime.date(*date)

    if date_to == 'today':
        date_to = datetime.date.today()
    else:
        date = tuple(map(int, date_to.split('-')))  # TODO: exception
        date_to = datetime.date(*date)

    if date_from > date_to:
        raise ValueError('date_from > date_to')

    try:
        ret, _ = _import_borme_download_range2(date_from, date_to, seccion,
                                               local_only, strict=no_missing)
        return ret
    except BormeDoesntExistException:
        logger.info("It looks like there is no BORME for this date ({}). "
                    "Nothing was downloaded".format(date_from))
        return False


def _import_borme_download_range2(begin, end, seccion, local_only,
                                  strict=False, create_json=True):
    """
    strict: Para en caso de error grave
    """
    next_date = begin
    total_results = {
        'created_anuncios': 0,
        'created_bormes': 0,
        'created_companies': 0,
        'created_persons': 0,
        'total_anuncios': 0,
        'total_bormes': 0,
        'total_companies': 0,
        'total_persons': 0,
        'errors': 0
    }
    total_start_time = time.time()

    try:
        while next_date and next_date <= end:
            xml_path = get_borme_xml_filepath(next_date)
            try:
                bxml = BormeXML.from_file(xml_path)
                if bxml.next_borme is None:
                    bxml = BormeXML.from_date(next_date)
                    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                    bxml.save_to_file(xml_path)

            except OSError:
                bxml = BormeXML.from_date(next_date)
                os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                bxml.save_to_file(xml_path)

            # Add FileHandlers
            directory = '%02d-%02d' % (bxml.date.year, bxml.date.month)
            logpath = os.path.join(settings.BORME_LOG_ROOT,
                                   'imports', directory)
            os.makedirs(logpath, exist_ok=True)

            fh1_path = os.path.join(logpath, '%02d_info.txt' % bxml.date.day)
            fh1 = logging.FileHandler(fh1_path)
            fh1.setLevel(logging.INFO)
            logger.addHandler(fh1)

            fh2_path = os.path.join(logpath, '%02d_error.txt' % bxml.date.day)
            fh2 = logging.FileHandler(fh2_path)
            fh2.setLevel(logging.WARNING)
            logger.addHandler(fh2)

            json_path = get_borme_json_path(bxml.date)
            pdf_path = get_borme_pdf_path(bxml.date)
            os.makedirs(pdf_path, exist_ok=True)
            logger.info("===================================================\n"
                        "Ran import_borme_download at {now}\n"
                        "  Import date: {borme_date}. Section: {section}\n"
                        "==================================================="
                        .format(now=timezone.now(),
                                borme_date=bxml.date.isoformat(),
                                section=seccion))
            print("\nPATH: {}"
                  "\nDATE: {}"
                  "\nSECCION: {}\n"
                  .format(pdf_path, bxml.date, seccion))

            bormes = []
            if not local_only:
                _, files = bxml.download_borme(pdf_path, seccion=seccion)

                for filepath in files:
                    if filepath.endswith('-99.pdf'):
                        continue
                    logger.info('%s' % filepath)
                    total_results['total_bormes'] += 1
                    try:
                        bormes.append(bormeparser.parse(filepath, seccion))
                    except Exception as e:
                        logger.error('[X] Error grave (I) en bormeparser.parse(): %s' % filepath)
                        logger.error('[X] %s: %s' % (e.__class__.__name__, e))
                        if strict:
                            logger.error('[X] Una vez arreglado, reanuda la importación:')
                            logger.error('[X]   python manage.py importbormetoday local')
                            return False, total_results

            else:
                cves = bxml.get_cves(bormeparser.SECCION.A)
                files_json = list(map(lambda x: os.path.join(json_path, '%s.json' % x), cves))
                files_pdf = list(map(lambda x: os.path.join(pdf_path, '%s.pdf' % x), cves))

                if files_exist(files_json):
                    for filepath in files_json:
                        logger.info('%s' % filepath)
                        total_results['total_bormes'] += 1
                        try:
                            bormes.append(bormeparser.Borme.from_json(filepath))
                        except Exception as e:
                            logger.error('[X] Error grave (I) en bormeparser.Borme.from_json(): %s' % filepath)
                            logger.error('[X] %s: %s' % (e.__class__.__name__, e))
                            if strict:
                                logger.error('[X] Una vez arreglado, reanuda la importación:')
                                logger.error('[X]   python manage.py importbormetoday local')  # TODO: --from date
                                return False, total_results
                elif files_exist(files_pdf):
                    for filepath in files_pdf:
                        logger.info('%s' % filepath)
                        total_results['total_bormes'] += 1
                        try:
                            bormes.append(bormeparser.parse(filepath, seccion))
                        except Exception as e:
                            logger.error('[X] Error grave (II) en bormeparser.parse(): %s' % filepath)
                            logger.error('[X] %s: %s' % (e.__class__.__name__, e))
                            if strict:
                                logger.error('[X] Una vez arreglado, reanuda la importación:')
                                logger.error('[X]   python manage.py importbormetoday local')  # TODO: --from date
                                return False, total_results
                else:
                    logger.error('[X] Faltan archivos PDF y JSON que no se desea descargar.')
                    logger.error('[X] JSON: %s' % ' '.join(files_json))
                    logger.error('[X] PDF: %s' % ' '.join(files_pdf))
                    if strict:
                        return False, total_results

                    for filepath in files_json:
                        if not os.path.exists(filepath):
                            logger.warn('[X] Missing JSON: %s' % filepath)
                            continue
                        logger.info('%s' % filepath)
                        total_results['total_bormes'] += 1
                        try:
                            bormes.append(bormeparser.Borme.from_json(filepath))
                        except Exception as e:
                            logger.error('[X] Error grave (II) en bormeparser.Borme.from_json(): %s' % filepath)
                            logger.error('[X] %s: %s' % (e.__class__.__name__, e))

            for borme in sorted(bormes):
                total_results['total_anuncios'] += len(borme.get_anuncios())
                start_time = time.time()
                try:
                    results = _import1(borme)
                except Exception as e:
                    logger.error('[%s] Error grave en _import1:' % borme.cve)
                    logger.error('[%s] %s' % (borme.cve, e))
                    logger.error('[%s] Prueba importar manualmente en modo detallado para ver el error:' % borme.cve)
                    logger.error('[%s]   python manage.py importbormepdf %s -v 3' % (borme.cve, borme.filename))
                    if strict:
                        logger.error('[%s] Una vez arreglado, reanuda la importación:' % borme.cve)
                        logger.error('[%s]   python manage.py importbormetoday local' % borme.cve)
                        return False, total_results

                if create_json:
                    os.makedirs(json_path, exist_ok=True)
                    json_filepath = os.path.join(json_path, '%s.json' % borme.cve)
                    borme.to_json(json_filepath)

                for key in total_results.keys():
                    total_results[key] += results[key]

                if not all(map(lambda x: x == 0, total_results.values())):
                    print_results(results, borme)
                    elapsed_time = time.time() - start_time
                    logger.info('[%s] Elapsed time: %.2f seconds' % (borme.cve, elapsed_time))

            # Remove handlers
            logger.removeHandler(fh1)
            logger.removeHandler(fh2)
            next_date = bxml.next_borme
    except KeyboardInterrupt:
        logger.info('\nImport aborted.')

    elapsed_time = time.time() - total_start_time
    logger.info("\nBORMEs creados: {created_bormes}/{total_bormes}\n"
                "Anuncios creados: {created_anuncios}/{total_anuncios}\n"
                "Empresas creadas: {created_companies}/{total_companies}\n"
                "Personas creadas: {created_persons}/{total_persons}"
                .format(**total_results))
    logger.info("Total elapsed time: %.2f seconds" % elapsed_time)

    return True, total_results


def import_borme_pdf(filename, create_json=True):
    """
    Import BORME PDF to database
    """
    results = {
        'created_anuncios': 0,
        'created_bormes': 0,
        'created_companies': 0,
        'created_persons': 0,
        'errors': 0
    }

    try:
        borme = bormeparser.parse(filename, bormeparser.SECCION.A)
        results = _import1(borme)
        if create_json:
            json_path = get_borme_json_path(borme.date)
            os.makedirs(json_path, exist_ok=True)
            json_filepath = os.path.join(json_path, '%s.json' % borme.cve)
            borme.to_json(json_filepath)
    except Exception as e:
        logger.error('[X] Error grave (III) en bormeparser.parse(): %s' % filename)
        logger.error('[X] %s: %s' % (e.__class__.__name__, e))

    if not all(map(lambda x: x == 0, results.values())):
        print_results(results, borme)
    return True, results


def import_borme_json(filename):
    """
    Import BORME JSON to database
    """
    results = {
        'created_anuncios': 0,
        'created_bormes': 0,
        'created_companies': 0,
        'created_persons': 0,
        'errors': 0
    }

    try:
        borme = bormeparser.Borme.from_json(filename)
        results = _import1(borme)
    except Exception as e:
        logger.error('[X] Error grave (III) en bormeparser.Borme.from_json(): %s' % filename)
        logger.error('[X] %s: %s' % (e.__class__.__name__, e))

    if not all(map(lambda x: x == 0, results.values())):
        print_results(results, borme)
    return True, results


def psql_update_documents():
    """
    Update postgresql full text search attributes.
    This function must be run everytime new records are added to the database
    """
    affected_rows = 0

    with connection.cursor() as cursor:
        cursor.execute("UPDATE borme_company "
                       "SET document = to_tsvector(name) "
                       "WHERE document IS NULL")
        affected_rows += cursor.rowcount
        logger.info("Updated {} borme_company records".format(cursor.rowcount))

    with connection.cursor() as cursor:
        cursor.execute("UPDATE borme_person "
                       "SET document = to_tsvector(name) "
                       "WHERE document IS NULL")
        affected_rows += cursor.rowcount
        logger.info("Updated {} borme_company records".format(cursor.rowcount))

    return affected_rows


def psql_update_documents_batch():
    """
    Same as psql_update_documents() but using atomic batches.
    This way, the table is not locked for a long time when the update is big
    """
    affected_rows = 0
    while Company.objects.filter(document__isnull=True).exists():
        with connection.cursor() as cursor:
            with transaction.atomic():
                for row in Company.objects.filter(document__isnull=True)[:1000]:
                    row.document = SearchVector("name")
                    row.save()
                    affected_rows += cursor.rowcount

            logger.info("Updated {} borme_company records"
                        .format(cursor.rowcount))

    while Person.objects.filter(document__isnull=True).exists():
        with connection.cursor() as cursor:
            with transaction.atomic():
                for row in Person.objects.filter(document__isnull=True)[:1000]:
                    row.document = SearchVector("name")
                    row.save()
                    affected_rows += cursor.rowcount

            logger.info("Updated {} borme_company records"
                        .format(cursor.rowcount))

    return affected_rows


def print_results(results, borme):
    log_message = "[{cve}] BORMEs creados: {bormes}/1\n" \
                  "[{cve}] Anuncios creados: {anuncios}/{total_anuncios}\n" \
                  "[{cve}] Empresas creadas: {companies}/{total_companies}\n" \
                  "[{cve}] Personas creadas: {persons}/{total_persons}" \
                  .format(cve=borme.cve, bormes=results['created_bormes'],
                          anuncios=results['created_anuncios'],
                          total_anuncios=len(borme.get_anuncios()),
                          companies=results['created_companies'],
                          total_companies=results['total_companies'],
                          persons=results['created_persons'],
                          total_persons=results['total_persons'])
    logger.info(log_message)
