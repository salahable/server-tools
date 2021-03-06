# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    This module copyright (C) 2014 Therp BV (<http://therp.nl>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp.osv import orm, fields
from openerp.tools.translate import _


class CleanupPurgeLineColumn(orm.TransientModel):
    _inherit = 'cleanup.purge.line'
    _name = 'cleanup.purge.line.column'

    _columns = {
        'model_id': fields.many2one(
            'ir.model', 'Model',
            required=True, ondelete='CASCADE'),
        'wizard_id': fields.many2one(
            'cleanup.purge.wizard.column', 'Purge Wizard', readonly=True),
        }

    def purge(self, cr, uid, ids, context=None):
        """
        Unlink columns upon manual confirmation.
        """
        for line in self.browse(cr, uid, ids, context=context):
            if line.purged:
                continue

            model_pool = self.pool[line.model_id.model]

            # Check whether the column actually still exists.
            # Inheritance such as stock.picking.in from stock.picking
            # can lead to double attempts at removal
            cr.execute(
                'SELECT count(attname) FROM pg_attribute '
                'WHERE attrelid = '
                '( SELECT oid FROM pg_class WHERE relname = %s ) '
                'AND attname = %s',
                (model_pool._table, line.name))
            if not cr.fetchone()[0]:
                continue

            self.logger.info(
                'Dropping column %s from table %s',
                line.name, model_pool._table)
            cr.execute(
                """
                ALTER TABLE "%s" DROP COLUMN "%s"
                """ % (model_pool._table, line.name))
            line.write({'purged': True})
            cr.commit()
        return True


class CleanupPurgeWizardColumn(orm.TransientModel):
    _inherit = 'cleanup.purge.wizard'
    _name = 'cleanup.purge.wizard.column'

    # List of known columns in use without corresponding fields
    # Format: {table: [fields]}
    blacklist = {
        'wkf_instance': ['uid'],  # lp:1277899
        }

    def default_get(self, cr, uid, fields, context=None):
        res = super(CleanupPurgeWizardColumn, self).default_get(
            cr, uid, fields, context=context)
        if 'name' in fields:
            res['name'] = _('Purge columns')
        return res

    def get_orphaned_columns(self, cr, uid, model_pools, context=None):
        """
        From openobject-server/openerp/osv/orm.py
        Iterate on the database columns to identify columns
        of fields which have been removed
        """

        columns = list(set([
            column for model_pool in model_pools
            for column in model_pool._columns
            if not (isinstance(model_pool._columns[column], fields.function)
                    and not model_pool._columns[column].store)
            ]))
        columns += orm.MAGIC_COLUMNS
        columns += self.blacklist.get(model_pools[0]._table, [])

        cr.execute("SELECT a.attname"
                   "  FROM pg_class c, pg_attribute a"
                   " WHERE c.relname=%s"
                   "   AND c.oid=a.attrelid"
                   "   AND a.attisdropped=%s"
                   "   AND pg_catalog.format_type(a.atttypid, a.atttypmod)"
                   "        NOT IN ('cid', 'tid', 'oid', 'xid')"
                   "   AND a.attname NOT IN %s",
                   (model_pools[0]._table, False, tuple(columns))),
        return [column[0] for column in cr.fetchall()]

    def find(self, cr, uid, context=None):
        """
        Search for columns that are not in the corresponding model.

        Group models by table to prevent false positives for columns
        that are only in some of the models sharing the same table.
        Example of this is 'sale_id' not being a field of stock.picking.in
        """
        res = []
        model_pool = self.pool['ir.model']
        model_ids = model_pool.search(cr, uid, [], context=context)

        # mapping of tables to tuples (model id, [pool1, pool2, ...])
        table2model = {}

        for model in model_pool.browse(cr, uid, model_ids, context=context):
            model_pool = self.pool.get(model.model)
            if not model_pool or not model_pool._auto:
                continue
            table2model.setdefault(
                model_pool._table, (model.id, []))[1].append(model_pool)

        for table, model_spec in table2model.iteritems():
            for column in self.get_orphaned_columns(
                    cr, uid, model_spec[1], context=context):
                res.append((0, 0, {
                            'name': column,
                            'model_id': model_spec[0]}))
        if not res:
            raise orm.except_orm(
                _('Nothing to do'),
                _('No orphaned columns found'))
        return res

    _columns = {
        'purge_line_ids': fields.one2many(
            'cleanup.purge.line.column',
            'wizard_id', 'Columns to purge'),
        }
