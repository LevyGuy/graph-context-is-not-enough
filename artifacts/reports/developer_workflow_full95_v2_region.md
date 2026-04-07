# Developer Workflow v2 Full 95

Instances evaluated: 95
Intended instances: 95
Failed instances: 0

## Candidate Discovery Metrics

- Exact symbol hit contains gold file: 43/95 (45.3%, 95% CI 35.6-55.3%)
- Grep hit contains gold file: 57/95 (60.0%, 95% CI 49.9-69.3%)
- Test hit contains gold file: 0/95 (0.0%, 95% CI 0.0-3.9%)
- Example hit contains gold file: 50/95 (52.6%, 95% CI 42.7-62.4%)
- Merged candidate top-3 contains gold file: 55/95 (57.9%, 95% CI 47.8-67.3%)
- Merged candidate top-5 contains gold file: 60/95 (63.2%, 95% CI 53.1-72.2%)

## Graph Expansion Metrics

- Expanded candidate set contains gold file: 72/95 (75.8%, 95% CI 66.3-83.3%)
- Expanded candidate set contains gold region: 72/95 (75.8%, 95% CI 66.3-83.3%)

## File Comparison Metrics

- File comparison top-1 is gold: 53/95 (55.8%, 95% CI 45.8-65.4%)
- File comparison top-3 contains gold: 59/95 (62.1%, 95% CI 52.1-71.2%)

## Selection Metrics

- Selected file is gold: 53/95 (55.8%, 95% CI 45.8-65.4%)
- Selected region is gold: 6/95 (6.3%, 95% CI 2.9-13.1%)

## Localization Metrics

- Retrieved Top-1 file match: 45/95 (47.4%, 95% CI 37.6-57.3%)
- Retrieved Top-3 file match: 55/95 (57.9%, 95% CI 47.8-67.3%)
- Retrieved Top-5 file match: 60/95 (63.2%, 95% CI 53.1-72.2%)
- Summary mentions gold file: 61/95 (64.2%, 95% CI 54.2-73.1%)
- Final target in gold file: 53/95 (55.8%, 95% CI 45.8-65.4%)
- Final target within gold hunk: 6/95 (6.3%, 95% CI 2.9-13.1%)
- Semantic correct file: 78/95 (82.1%, 95% CI 73.2-88.5%)
- Semantic correct function: 66/95 (69.5%, 95% CI 59.6-77.8%)
- Semantic correct fix mechanism: 83/95 (87.4%, 95% CI 79.2-92.6%)
- Semantic localization match: 63/95 (66.3%, 95% CI 56.3-75.0%)
- Weak workflow found issue: 76/95 (80.0%, 95% CI 70.9-86.8%)

## Repo Breakdown

| Repo | Sample | Semantic Localization | Weak Workflow Found Issue |
|---|---:|---|---|
| astropy/astropy | 4 | 4/4 (100.0%, 95% CI 51.0-100.0%) | 4/4 (100.0%, 95% CI 51.0-100.0%) |
| django/django | 38 | 27/38 (71.1%, 95% CI 55.2-83.0%) | 34/38 (89.5%, 95% CI 75.9-95.8%) |
| matplotlib/matplotlib | 9 | 4/9 (44.4%, 95% CI 18.9-73.3%) | 6/9 (66.7%, 95% CI 35.4-87.9%) |
| mwaskom/seaborn | 3 | 2/3 (66.7%, 95% CI 20.8-93.9%) | 2/3 (66.7%, 95% CI 20.8-93.9%) |
| pallets/flask | 3 | 3/3 (100.0%, 95% CI 43.8-100.0%) | 3/3 (100.0%, 95% CI 43.8-100.0%) |
| psf/requests | 4 | 3/4 (75.0%, 95% CI 30.1-95.4%) | 3/4 (75.0%, 95% CI 30.1-95.4%) |
| pydata/xarray | 4 | 3/4 (75.0%, 95% CI 30.1-95.4%) | 3/4 (75.0%, 95% CI 30.1-95.4%) |
| pylint-dev/pylint | 4 | 1/4 (25.0%, 95% CI 4.6-69.9%) | 2/4 (50.0%, 95% CI 15.0-85.0%) |
| pytest-dev/pytest | 8 | 3/8 (37.5%, 95% CI 13.7-69.4%) | 3/8 (37.5%, 95% CI 13.7-69.4%) |
| scikit-learn/scikit-learn | 9 | 9/9 (100.0%, 95% CI 70.1-100.0%) | 9/9 (100.0%, 95% CI 70.1-100.0%) |
| sphinx-doc/sphinx | 7 | 3/7 (42.9%, 95% CI 15.8-75.0%) | 5/7 (71.4%, 95% CI 35.9-91.8%) |
| sympy/sympy | 2 | 1/2 (50.0%, 95% CI 9.5-90.5%) | 2/2 (100.0%, 95% CI 34.2-100.0%) |

## Failure Taxonomy

| Bucket | Count |
|---|---:|
| localized successfully | 63 |
| deterministic candidate discovery missed correct file | 20 |
| comparison preferred wrong file despite good evidence | 4 |
| file chosen correctly but region selection missed | 4 |
| issue likely requires runtime execution/reproduction | 2 |
| LLM summary ignored strong evidence | 1 |
| selector chose wrong target from correct candidate set | 1 |

## Per-Instance Results

| Instance | Repo | Gold Files | Top Candidates | Target | Merged Top-3 | Expanded Gold | Semantic | Taxonomy | Workflow Help |
|---|---|---|---|---|---|---|---|---|---|
| astropy__astropy-12907 | astropy/astropy | astropy/modeling/separable.py | astropy/modeling/separable.py, astropy/modeling/core.py, astropy/modeling/functional_models.py | astropy/modeling/separable.py:66 | yes | yes | yes | localized successfully | high |
| astropy__astropy-14995 | astropy/astropy | astropy/nddata/mixins/ndarithmetic.py | astropy/nddata/nddata_withmixins.py, astropy/nddata/nddata.py, astropy/nddata/bitmask.py | astropy/nddata/nddata_withmixins.py:16 | no | no | yes | localized successfully | high |
| astropy__astropy-6938 | astropy/astropy | astropy/io/fits/fitsrec.py | astropy/io/fits/fitsrec.py, astropy/io/fits/util.py, astropy/io/fits/column.py | astropy/io/fits/fitsrec.py:18 | yes | yes | yes | localized successfully | high |
| astropy__astropy-7746 | astropy/astropy | astropy/wcs/wcs.py | astropy/wcs/wcs.py, astropy/utils/codegen.py, astropy/samp/integrated_client.py | astropy/wcs/wcs.py:1349 | yes | yes | yes | localized successfully | high |
| django__django-11019 | django/django | django/forms/widgets.py | django/forms/widgets.py, django/forms/fields.py, django/forms/forms.py | django/forms/widgets.py:36 | yes | yes | no | file chosen correctly but region selection missed | high |
| django__django-11049 | django/django | django/db/models/fields/__init__.py | django/db/models/fields/__init__.py, django/forms/fields.py, django/db/models/functions/datetime.py | django/db/models/fields/__init__.py:88 | yes | yes | yes | localized successfully | high |
| django__django-11099 | django/django | django/contrib/auth/validators.py | django/contrib/auth/validators.py, django/contrib/auth/migrations/0004_alter_user_username_opts.py, django/contrib/auth/migrations/0008_alter_user_username_max_length.py | django/contrib/auth/validators.py:8 | yes | yes | yes | localized successfully | high |
| django__django-11133 | django/django | django/http/response.py | django/http/response.py, django/db/models/fields/__init__.py, django/conf/global_settings.py | django/http/response.py:278 | yes | yes | yes | localized successfully | high |
| django__django-11422 | django/django | django/utils/autoreload.py | django/utils/autoreload.py, django/contrib/admin/templatetags/__init__.py, django/contrib/admin/templatetags/admin_list.py | django/utils/autoreload.py:323 | yes | yes | yes | localized successfully | medium-high |
| django__django-11564 | django/django | django/conf/__init__.py | django/contrib/staticfiles/storage.py, django/conf/global_settings.py, django/core/files/storage.py | django/contrib/staticfiles/storage.py:144 | no | no | yes | localized successfully | high |
| django__django-11815 | django/django | django/db/migrations/serializer.py | django/forms/fields.py, django/db/models/fields/__init__.py, django/contrib/admin/migrations/__init__.py | django/contrib/admin/migrations/__init__.py:1 | no | yes | no | comparison preferred wrong file despite good evidence | high |
| django__django-11905 | django/django | django/db/models/lookups.py | django/db/models/lookups.py, django/db/models/sql/query.py, django/db/models/query.py | django/db/models/lookups.py:15 | yes | yes | yes | localized successfully | high |
| django__django-11910 | django/django | django/db/migrations/autodetector.py | django/db/migrations/operations/fields.py, django/forms/fields.py, django/db/models/fields/__init__.py | django/db/migrations/operations/fields.py:103 | no | no | yes | localized successfully | high |
| django__django-11964 | django/django | django/db/models/enums.py | django/db/models/enums.py, django/db/models/fields/__init__.py, django/forms/fields.py | django/db/models/enums.py:8 | yes | yes | no | file chosen correctly but region selection missed | high |
| django__django-12308 | django/django | django/contrib/admin/utils.py | django/forms/fields.py, django/contrib/postgres/fields/jsonb.py, django/db/models/fields/json.py | django/contrib/admin/utils.py:380 | no | yes | yes | localized successfully | high |
| django__django-12497 | django/django | django/db/models/fields/related.py | django/db/models/fields/related.py, django/core/serializers/xml_serializer.py, django/db/models/fields/__init__.py | django/db/models/fields/related.py:83 | yes | yes | yes | localized successfully | high |
| django__django-12700 | django/django | django/views/debug.py | django/views/debug.py, django/conf/global_settings.py, django/views/decorators/debug.py | django/views/debug.py:31 | yes | yes | yes | localized successfully | high |
| django__django-12983 | django/django | django/utils/text.py | django/utils/text.py, django/contrib/admindocs/utils.py, django/contrib/gis/db/backends/utils.py | django/utils/text.py:393 | yes | yes | yes | localized successfully | high |
| django__django-13220 | django/django | django/core/exceptions.py | django/core/exceptions.py, django/core/validators.py, django/contrib/postgres/validators.py | django/core/exceptions.py:99 | yes | yes | yes | localized successfully | high |
| django__django-13265 | django/django | django/db/migrations/autodetector.py | django/db/migrations/operations/models.py, django/db/models/fields/__init__.py, django/forms/fields.py | django/db/migrations/operations/models.py:572 | no | yes | no | deterministic candidate discovery missed correct file | high |
| django__django-13447 | django/django | django/contrib/admin/sites.py | django/contrib/admin/sites.py, django/contrib/admin/__init__.py, django/contrib/admin/actions.py | django/contrib/admin/sites.py:433 | yes | yes | yes | localized successfully | high |
| django__django-13590 | django/django | django/db/models/sql/query.py | django/db/models/sql/query.py, django/utils/datastructures.py, django/conf/__init__.py | django/db/models/sql/query.py:1072 | yes | yes | yes | localized successfully | high |
| django__django-13658 | django/django | django/core/management/__init__.py | django/core/management/__init__.py, django/core/management/base.py, django/__main__.py | django/core/management/__init__.py:184 | yes | yes | yes | localized successfully | high |
| django__django-13933 | django/django | django/forms/models.py | django/forms/models.py, django/forms/widgets.py, django/forms/fields.py | django/forms/models.py:1179 | yes | yes | yes | localized successfully | high |
| django__django-14016 | django/django | django/db/models/query_utils.py | django/forms/models.py, django/contrib/contenttypes/models.py, django/template/defaulttags.py | django/contrib/admin/models.py:23 | no | no | no | deterministic candidate discovery missed correct file | medium-high |
| django__django-14017 | django/django | django/db/models/query_utils.py | django/db/models/expressions.py, django/db/models/query_utils.py, django/contrib/postgres/search.py | django/db/models/expressions.py:58 | yes | yes | no | comparison preferred wrong file despite good evidence | high |
| django__django-14238 | django/django | django/db/models/fields/__init__.py | django/db/models/fields/__init__.py, django/db/models/options.py, django/db/models/base.py | django/db/models/fields/__init__.py:196 | yes | yes | no | file chosen correctly but region selection missed | high |
| django__django-14411 | django/django | django/contrib/auth/forms.py | django/contrib/auth/forms.py, django/contrib/admin/helpers.py, django/contrib/admindocs/urls.py | django/contrib/auth/forms.py:33 | yes | yes | yes | localized successfully | high |
| django__django-14534 | django/django | django/forms/boundfield.py | django/forms/boundfield.py, django/forms/widgets.py, django/contrib/postgres/forms/array.py | django/forms/boundfield.py:36 | yes | yes | yes | localized successfully | high |
| django__django-14580 | django/django | django/db/migrations/serializer.py | django/db/models/fields/__init__.py, django/db/migrations/operations/models.py, django/contrib/admin/migrations/__init__.py | django/db/migrations/operations/models.py:23 | no | no | yes | localized successfully | high |
| django__django-14672 | django/django | django/db/models/fields/reverse_related.py | django/db/models/fields/__init__.py, django/db/models/fields/reverse_related.py, django/core/checks/model_checks.py | django/db/models/fields/reverse_related.py:119 | yes | yes | yes | localized successfully | high |
| django__django-15061 | django/django | django/forms/widgets.py | django/forms/widgets.py, django/forms/boundfield.py, django/contrib/admin/widgets.py | django/forms/widgets.py:268 | yes | yes | yes | localized successfully | high |
| django__django-15388 | django/django | django/template/autoreload.py | django/conf/global_settings.py, django/test/signals.py, django/template/utils.py | django/conf/global_settings.py:222 | no | no | no | issue likely requires runtime execution/reproduction | medium-high |
| django__django-15781 | django/django | django/core/management/base.py | django/db/models/options.py, django/contrib/gis/gdal/geometries.py, django/db/backends/base/introspection.py | django/db/models/options.py:515 | no | no | no | deterministic candidate discovery missed correct file | medium |
| django__django-16041 | django/django | django/forms/formsets.py | django/forms/formsets.py, django/template/defaulttags.py, django/views/generic/edit.py | django/forms/formsets.py:258 | yes | yes | yes | localized successfully | high |
| django__django-16229 | django/django | django/forms/boundfield.py | django/contrib/admin/options.py, django/forms/models.py, django/db/models/fields/related.py | django/contrib/admin/options.py:117 | no | no | no | deterministic candidate discovery missed correct file | high |
| django__django-16408 | django/django | django/db/models/sql/compiler.py | django/db/models/query.py, django/test/testcases.py, django/db/models/fields/related_descriptors.py | django/db/models/query.py:290 | no | no | no | issue likely requires runtime execution/reproduction | medium-high |
| django__django-16527 | django/django | django/contrib/admin/templatetags/admin_modify.py | django/contrib/admin/options.py, django/contrib/admin/templatetags/admin_modify.py, django/contrib/auth/admin.py | django/contrib/admin/templatetags/admin_modify.py:61 | yes | yes | yes | localized successfully | high |
| django__django-16595 | django/django | django/db/migrations/operations/fields.py | django/db/migrations/operations/fields.py, django/forms/fields.py, django/db/models/fields/__init__.py | django/db/migrations/operations/fields.py:25 | yes | yes | yes | localized successfully | high |
| django__django-16873 | django/django | django/template/defaultfilters.py | django/test/testcases.py, django/template/loader.py, django/template/engine.py | django/template/backends/django.py:56 | no | no | yes | localized successfully | high |
| django__django-16910 | django/django | django/db/models/sql/query.py | django/db/models/query.py, django/db/models/sql/query.py, django/db/models/lookups.py | django/db/models/query.py:309 | yes | yes | yes | localized successfully | medium-high |
| django__django-17051 | django/django | django/db/models/query.py | django/db/models/query.py, django/db/models/sql/query.py, django/db/models/fields/related_descriptors.py | django/db/models/query.py:726 | yes | yes | yes | localized successfully | medium |
| matplotlib__matplotlib-22711 | matplotlib/matplotlib | lib/matplotlib/widgets.py | lib/matplotlib/widgets.py, lib/matplotlib/figure.py, lib/matplotlib/pyplot.py | lib/matplotlib/widgets.py:545 | yes | yes | yes | localized successfully | high |
| matplotlib__matplotlib-23299 | matplotlib/matplotlib | lib/matplotlib/__init__.py | lib/matplotlib/pyplot.py, lib/matplotlib/__init__.py, lib/matplotlib/backends/backend_qt.py | lib/matplotlib/pyplot.py:589 | yes | yes | no | comparison preferred wrong file despite good evidence | high |
| matplotlib__matplotlib-23314 | matplotlib/matplotlib | lib/mpl_toolkits/mplot3d/axes3d.py | lib/matplotlib/gridspec.py, lib/mpl_toolkits/axes_grid1/mpl_axes.py, lib/matplotlib/artist.py | lib/mpl_toolkits/axes_grid1/mpl_axes.py:19 | no | no | no | deterministic candidate discovery missed correct file | high |
| matplotlib__matplotlib-23913 | matplotlib/matplotlib | lib/matplotlib/legend.py | lib/matplotlib/patheffects.py, lib/matplotlib/axes/_secondary_axes.py, lib/mpl_toolkits/axisartist/grid_helper_curvelinear.py | lib/matplotlib/patheffects.py:25 | no | no | no | deterministic candidate discovery missed correct file | medium |
| matplotlib__matplotlib-24265 | matplotlib/matplotlib | lib/matplotlib/style/core.py | lib/matplotlib/__init__.py, tools/gh_api.py, lib/matplotlib/axis.py | lib/matplotlib/__init__.py:203 | no | yes | no | deterministic candidate discovery missed correct file | high |
| matplotlib__matplotlib-24334 | matplotlib/matplotlib | lib/matplotlib/axis.py | lib/matplotlib/colorbar.py, lib/matplotlib/axes/_base.py, lib/matplotlib/pyplot.py | lib/matplotlib/axes/_base.py:3401 | no | yes | no | LLM summary ignored strong evidence | high |
| matplotlib__matplotlib-25442 | matplotlib/matplotlib | lib/matplotlib/offsetbox.py | lib/matplotlib/offsetbox.py, lib/matplotlib/patheffects.py, lib/matplotlib/backend_bases.py | lib/matplotlib/offsetbox.py:1543 | yes | yes | yes | localized successfully | high |
| matplotlib__matplotlib-25498 | matplotlib/matplotlib | lib/matplotlib/colorbar.py | lib/matplotlib/colorbar.py, lib/matplotlib/_pylab_helpers.py, lib/matplotlib/pyplot.py | lib/matplotlib/colorbar.py:493 | yes | yes | yes | localized successfully | high |
| matplotlib__matplotlib-26011 | matplotlib/matplotlib | lib/matplotlib/axis.py | lib/matplotlib/axes/_base.py, galleries/examples/misc/custom_projection.py, lib/matplotlib/projections/geo.py | lib/matplotlib/axes/_base.py:847 | no | yes | yes | localized successfully | high |
| mwaskom__seaborn-2848 | mwaskom/seaborn | seaborn/_oldcore.py | seaborn/utils.py, seaborn/categorical.py, seaborn/_core/plot.py | seaborn/_core/plot.py:110 | no | yes | yes | localized successfully | high |
| mwaskom__seaborn-3010 | mwaskom/seaborn | seaborn/_stats/regression.py | seaborn/_core/plot.py, seaborn/_core/groupby.py, seaborn/_stats/regression.py | seaborn/_stats/regression.py:10 | yes | yes | yes | localized successfully | medium |
| mwaskom__seaborn-3407 | mwaskom/seaborn | seaborn/axisgrid.py | seaborn/axisgrid.py, seaborn/_marks/base.py, seaborn/_stats/density.py | seaborn/axisgrid.py:1431 | yes | yes | no | file chosen correctly but region selection missed | high |
| pallets__flask-4045 | pallets/flask | src/flask/blueprints.py | src/flask/blueprints.py, src/flask/app.py, src/flask/scaffold.py | src/flask/blueprints.py:32 | yes | yes | yes | localized successfully | high |
| pallets__flask-4992 | pallets/flask | src/flask/config.py | src/flask/config.py, src/flask/scaffold.py, src/flask/app.py | src/flask/config.py:232 | yes | yes | yes | localized successfully | high |
| pallets__flask-5063 | pallets/flask | src/flask/cli.py | src/flask/app.py, src/flask/blueprints.py, src/flask/scaffold.py | src/flask/app.py:105 | no | yes | yes | localized successfully | medium |
| psf__requests-1963 | psf/requests | requests/sessions.py | requests/sessions.py, requests/models.py, requests/packages/urllib3/connectionpool.py | requests/sessions.py:84 | yes | yes | yes | localized successfully | high |
| psf__requests-2674 | psf/requests | requests/adapters.py | requests/packages/urllib3/exceptions.py, requests/packages/urllib3/response.py, requests/models.py | requests/packages/urllib3/exceptions.py:46 | no | yes | no | deterministic candidate discovery missed correct file | medium |
| psf__requests-3362 | psf/requests | requests/utils.py | requests/models.py, tests/test_requests.py, requests/__init__.py | requests/models.py:653 | no | yes | yes | localized successfully | high |
| psf__requests-863 | psf/requests | requests/models.py | requests/models.py, requests/auth.py, requests/packages/urllib3/packages/ordered_dict.py | requests/models.py:43 | yes | yes | yes | localized successfully | high |
| pydata__xarray-3364 | pydata/xarray | xarray/core/concat.py | xarray/core/missing.py, xarray/coding/variables.py, xarray/core/concat.py | xarray/core/concat.py:10 | yes | yes | yes | localized successfully | high |
| pydata__xarray-4094 | pydata/xarray | xarray/core/dataarray.py | xarray/core/dataarray.py, xarray/core/dataset.py, xarray/util/print_versions.py | xarray/core/dataset.py:402 | yes | yes | no | comparison preferred wrong file despite good evidence | high |
| pydata__xarray-4248 | pydata/xarray | xarray/core/formatting.py | xarray/core/dataset.py, xarray/core/coordinates.py, xarray/coding/times.py | xarray/core/dataset.py:399 | no | no | yes | localized successfully | medium |
| pydata__xarray-5131 | pydata/xarray | xarray/core/groupby.py | xarray/core/groupby.py, xarray/core/dataset.py, xarray/conventions.py | xarray/core/groupby.py:416 | yes | yes | yes | localized successfully | medium |
| pylint-dev__pylint-7080 | pylint-dev/pylint | pylint/lint/expand_modules.py | pylint/checkers/base/name_checker/naming_style.py, pylint/checkers/design_analysis.py, pylint/checkers/format.py | pylint/checkers/base/name_checker/naming_style.py:30 | no | no | no | deterministic candidate discovery missed correct file | medium |
| pylint-dev__pylint-7114 | pylint-dev/pylint | pylint/lint/expand_modules.py | pylint/testutils/functional/lint_module_output_update.py, doc/data/messages/l/lost-exception/bad.py, doc/data/messages/l/lost-exception/good.py | pylint/testutils/functional/lint_module_output_update.py:31 | no | no | no | deterministic candidate discovery missed correct file | high |
| pylint-dev__pylint-7228 | pylint-dev/pylint | pylint/config/argument.py | pylint/config/arguments_manager.py, pylint/config/config_initialization.py, pylint/__init__.py | pylint/config/config_initialization.py:20 | no | yes | no | deterministic candidate discovery missed correct file | high |
| pylint-dev__pylint-7993 | pylint-dev/pylint | pylint/reporters/text.py | pylint/checkers/base/comparison_checker.py, pylint/testutils/_primer/primer_run_command.py, pylint/testutils/output_line.py | pylint/reporters/text.py:73 | no | yes | yes | localized successfully | high |
| pytest-dev__pytest-11143 | pytest-dev/pytest | src/_pytest/assertion/rewrite.py | src/_pytest/assertion/rewrite.py, src/_pytest/python.py, src/_pytest/runner.py | src/_pytest/assertion/rewrite.py:744 | yes | yes | yes | localized successfully | high |
| pytest-dev__pytest-5495 | pytest-dev/pytest | src/_pytest/assertion/util.py | testing/acceptance_test.py, testing/python/metafunc.py, src/_pytest/assertion/rewrite.py | src/_pytest/assertion/rewrite.py:30 | no | yes | no | deterministic candidate discovery missed correct file | high |
| pytest-dev__pytest-6116 | pytest-dev/pytest | src/_pytest/main.py | src/_pytest/config/__init__.py, src/_pytest/hookspec.py, src/_pytest/pytester.py | src/_pytest/config/__init__.py:60 | no | yes | no | selector chose wrong target from correct candidate set | high |
| pytest-dev__pytest-7168 | pytest-dev/pytest | src/_pytest/_io/saferepr.py | src/_pytest/main.py, src/_pytest/runner.py, src/_pytest/pytester.py | src/_pytest/main.py:413 | no | yes | no | deterministic candidate discovery missed correct file | high |
| pytest-dev__pytest-7220 | pytest-dev/pytest | src/_pytest/nodes.py | src/_pytest/_code/code.py, src/_pytest/assertion/rewrite.py, testing/acceptance_test.py | src/_pytest/pytester.py:624 | no | no | no | deterministic candidate discovery missed correct file | high |
| pytest-dev__pytest-7373 | pytest-dev/pytest | src/_pytest/mark/evaluate.py | src/_pytest/mark/evaluate.py, src/_pytest/pytester.py, src/_pytest/cacheprovider.py | src/_pytest/mark/evaluate.py:21 | yes | yes | yes | localized successfully | high |
| pytest-dev__pytest-8906 | pytest-dev/pytest | src/_pytest/python.py | bench/skip.py, testing/python/fixtures.py, testing/plugins_integration/simple_integration.py | bench/skip.py:6 | no | yes | no | deterministic candidate discovery missed correct file | high |
| pytest-dev__pytest-9359 | pytest-dev/pytest | src/_pytest/_code/source.py | src/_pytest/_code/code.py, src/_pytest/legacypath.py, src/_pytest/main.py | src/_pytest/assertion/rewrite.py:649 | no | no | yes | localized successfully | high |
| scikit-learn__scikit-learn-10297 | scikit-learn/scikit-learn | sklearn/linear_model/ridge.py | sklearn/linear_model/ridge.py, sklearn/cross_validation.py, sklearn/linear_model/stochastic_gradient.py | sklearn/linear_model/ridge.py:461 | yes | yes | yes | localized successfully | medium |
| scikit-learn__scikit-learn-10508 | scikit-learn/scikit-learn | sklearn/preprocessing/label.py | sklearn/preprocessing/label.py, sklearn/grid_search.py, sklearn/pipeline.py | sklearn/preprocessing/label.py:115 | yes | yes | yes | localized successfully | high |
| scikit-learn__scikit-learn-10949 | scikit-learn/scikit-learn | sklearn/utils/validation.py | sklearn/utils/validation.py, sklearn/exceptions.py, sklearn/preprocessing/data.py | sklearn/utils/validation.py:354 | yes | yes | yes | localized successfully | high |
| scikit-learn__scikit-learn-11281 | scikit-learn/scikit-learn | sklearn/mixture/base.py | sklearn/mixture/gmm.py, sklearn/neighbors/lof.py, sklearn/cluster/dbscan_.py | sklearn/mixture/gmm.py:133 | no | no | yes | localized successfully | high |
| scikit-learn__scikit-learn-13779 | scikit-learn/scikit-learn | sklearn/ensemble/voting.py | sklearn/ensemble/voting.py, sklearn/pipeline.py, sklearn/linear_model/stochastic_gradient.py | sklearn/ensemble/voting.py:30 | yes | yes | yes | localized successfully | high |
| scikit-learn__scikit-learn-14092 | scikit-learn/scikit-learn | sklearn/neighbors/nca.py | sklearn/neighbors/nca.py, sklearn/model_selection/_search.py, sklearn/linear_model/logistic.py | sklearn/neighbors/nca.py:158 | yes | yes | yes | localized successfully | high |
| scikit-learn__scikit-learn-14894 | scikit-learn/scikit-learn | sklearn/svm/base.py | sklearn/preprocessing/data.py, sklearn/svm/base.py, sklearn/impute/_base.py | sklearn/svm/base.py:58 | yes | yes | yes | localized successfully | high |
| scikit-learn__scikit-learn-15512 | scikit-learn/scikit-learn | sklearn/cluster/_affinity_propagation.py | sklearn/cluster/_affinity_propagation.py, sklearn/ensemble/_hist_gradient_boosting/gradient_boosting.py, sklearn/inspection/_permutation_importance.py | sklearn/cluster/_affinity_propagation.py:33 | yes | yes | yes | localized successfully | medium |
| scikit-learn__scikit-learn-25500 | scikit-learn/scikit-learn | sklearn/isotonic.py | sklearn/calibration.py, sklearn/linear_model/_stochastic_gradient.py, sklearn/model_selection/_validation.py | sklearn/calibration.py:55 | no | no | yes | localized successfully | high |
| sphinx-doc__sphinx-11445 | sphinx-doc/sphinx | sphinx/util/rst.py | sphinx/util/rst.py, sphinx/application.py, sphinx/builders/html/__init__.py | sphinx/util/rst.py:54 | yes | yes | yes | localized successfully | high |
| sphinx-doc__sphinx-7686 | sphinx-doc/sphinx | sphinx/ext/autosummary/generate.py | sphinx/util/template.py, sphinx/ext/autosummary/__init__.py, sphinx/builders/gettext.py | sphinx/ext/autosummary/__init__.py:109 | no | yes | no | deterministic candidate discovery missed correct file | high |
| sphinx-doc__sphinx-7738 | sphinx-doc/sphinx | sphinx/ext/napoleon/docstring.py | sphinx/io.py, sphinx/errors.py, sphinx/builders/linkcheck.py | sphinx/ext/napoleon/iterators.py:17 | no | no | no | deterministic candidate discovery missed correct file | high |
| sphinx-doc__sphinx-7975 | sphinx-doc/sphinx | sphinx/environment/adapters/indexentries.py | sphinx/domains/c.py, sphinx/domains/cpp.py, sphinx/environment/adapters/indexentries.py | sphinx/environment/adapters/indexentries.py:28 | yes | yes | yes | localized successfully | high |
| sphinx-doc__sphinx-8474 | sphinx-doc/sphinx | sphinx/domains/std.py | sphinx/__init__.py, sphinx/application.py, sphinx/builders/singlehtml.py | sphinx/builders/singlehtml.py:29 | no | no | no | deterministic candidate discovery missed correct file | medium |
| sphinx-doc__sphinx-8506 | sphinx-doc/sphinx | sphinx/domains/std.py | sphinx/deprecation.py, sphinx/__init__.py, sphinx/__main__.py | sphinx/application.py:129 | no | no | no | deterministic candidate discovery missed correct file | medium |
| sphinx-doc__sphinx-8721 | sphinx-doc/sphinx | sphinx/ext/viewcode.py | sphinx/application.py, sphinx/__main__.py, sphinx/ext/viewcode.py | sphinx/ext/viewcode.py:53 | yes | yes | yes | localized successfully | high |
| sympy__sympy-11870 | sympy/sympy | sympy/functions/elementary/trigonometric.py | - | sympy/simplify/trigsimp.py:0 | no | no | no | deterministic candidate discovery missed correct file | high |
| sympy__sympy-12481 | sympy/sympy | sympy/combinatorics/permutations.py | sympy/combinatorics/permutations.py, release/fabfile.py, sympy/matrices/sparse.py | sympy/combinatorics/permutations.py:425 | yes | yes | yes | localized successfully | high |
